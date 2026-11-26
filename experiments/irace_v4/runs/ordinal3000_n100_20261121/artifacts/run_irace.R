# irace 4.5 driver. The Windows-compatible targetRunner delegates each solver
# evaluation to Python; PSOCK workers execute up to --parallel evaluations.
argv <- commandArgs(trailingOnly=TRUE)
getarg <- function(name, default=NULL) {
  at <- match(paste0("--",name),argv)
  if(is.na(at)) return(default)
  argv[at+1L]
}
run_dir <- normalizePath(getarg("run-dir"),winslash="/",mustWork=TRUE)
python <- normalizePath(getarg("python"),winslash="/",mustWork=TRUE)
training <- normalizePath(getarg("training-dir"),winslash="/",mustWork=TRUE)
training_list <- file.path(run_dir,"artifacts","train_instances.txt")
if(!file.exists(training_list)) training_list <- file.path(training,"train_instances.txt")
budget <- as.integer(getarg("budget","15000"))
workers <- as.integer(getarg("parallel","20"))
irace_seed <- as.integer(getarg("seed","20261112"))
recovery <- getarg("recovery","")
Sys.setenv(IRACE_PYTHON=python,OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
library(irace)
if(as.character(packageVersion("irace")) != "4.5") stop("This campaign pins irace 4.5")

# This wrapper changes checkpoint I/O only, not racing, scoring or sampling.
# A crash while saving leaves the previous immutable checkpoints available.
original_save <- get("save_irace_logfile",asNamespace("irace"))
if(!identical(names(formals(original_save)),c("iraceResults","logfile")))
  stop("Unsupported irace checkpoint API")
durable_save <- local({
  save_impl <- original_save
  python_exec <- python
  publisher <- file.path(run_dir,"artifacts","publish_checkpoint.py")
  function(iraceResults,logfile) {
    if(is.null(logfile) || !nzchar(logfile)) return(invisible(NULL))
    temporary <- paste0(logfile,".saving-",Sys.getpid())
    save_impl(iraceResults,temporary)
    status <- system2(python_exec,shQuote(c(publisher,temporary,logfile)))
    if(status != 0L) stop("Could not publish a durable irace checkpoint")
    invisible(NULL)
  }
})
assignInNamespace("save_irace_logfile",durable_save,ns="irace")

# Reuse independently drawn solver seeds for shared instances in a NEW race.
# The original function still generates the new 20-instance schedule and consumes
# the same RNG stream. Only explicitly recorded first-visit seeds are replaced;
# no old costs, eliminations, elites, or race state are injected into irace.
seed_file <- file.path(run_dir,"artifacts","seed_reuse_schedule.csv")
if(file.exists(seed_file)) {
  seed_data <- read.csv(seed_file,stringsAsFactors=FALSE)
  seed_data <- seed_data[order(seed_data$instance,seed_data$occurrence),]
  seed_map <- split(seed_data$seed,seed_data$instance)
  original_generate <- get("generateInstances",asNamespace("irace"))
  if(!identical(names(formals(original_generate)),c("race_state","scenario","n","update")))
    stop("Unsupported irace instance-schedule API")
  reuse_generate <- local({
    generate_impl <- original_generate
    recorded_seeds <- seed_map
    function(race_state,scenario,n,update=FALSE) {
      generate_impl(race_state,scenario,n,update)
      seen <- new.env(parent=emptyenv())
      for(i in seq_len(nrow(race_state$instances_log))) {
        name <- basename(scenario$instances[[race_state$instances_log$instanceID[i]]])
        occurrence <- if(exists(name,seen,inherits=FALSE)) get(name,seen)+1L else 1L
        assign(name,occurrence,seen)
        seeds <- recorded_seeds[[name]]
        if(length(seeds)>=occurrence)
          race_state$instances_log$seed[i] <- as.integer(seeds[[occurrence]])
      }
      race_state$instances_log
    }
  })
  assignInNamespace("generateInstances",reuse_generate,ns="irace")
}

runner <- function(experiment,scenario) {
  parameter_names <- c("cseg","h_gap","T0","alpha","rho","kappa","tau_v","r_destroy")
  configuration <- unlist(experiment$configuration,use.names=TRUE)[parameter_names]
  if(anyNA(configuration)) stop("Missing a tuned parameter")
  switches <- paste0("--",gsub("_","-",parameter_names),"=",configuration)
  error_dir <- file.path(scenario$execDir,"runner_errors")
  dir.create(error_dir,showWarnings=FALSE)
  error_file <- tempfile(pattern=paste0(experiment$id_configuration,"-",experiment$id_instance,"-"),tmpdir=error_dir,fileext=".log")
  arguments <- c(file.path(scenario$execDir,"artifacts","target_runner.py"),
                 "--run-dir",scenario$execDir,"--instance",experiment$instance,
                 "--seed",sprintf("%.0f",as.numeric(experiment$seed)),
                 "--configuration-id",as.character(experiment$id_configuration),
                 "--instance-id",as.character(experiment$id_instance),switches)
  output <- system2(Sys.getenv("IRACE_PYTHON"),shQuote(arguments),stdout=TRUE,stderr=error_file)
  status <- attr(output,"status")
  if(!is.null(status) && status != 0L) stop(paste("Target runner failed; inspect",error_file))
  values <- scan(text=paste(output,collapse=" "),quiet=TRUE)
  if(length(values)!=2L || any(!is.finite(values))) stop("Target runner must return finite cost and time")
  list(cost=values[1],time=values[2])
}

scenario <- list(execDir=run_dir,
                 parameters=irace::readParameters(file.path(run_dir,"artifacts","parameters.txt"),digits=8L),
                 configurationsFile=file.path(run_dir,"artifacts","initial_configurations.txt"),
                 trainInstancesDir=file.path(training,"instances"),
                 trainInstancesFile=training_list,
                 targetRunner=runner,parallel=workers,loadBalancing=TRUE,
                 maxExperiments=budget,seed=irace_seed,deterministic=FALSE,
                 capping=FALSE,maxTime=0,logFile=file.path(run_dir,"irace.Rdata"),
                 recoveryFile=recovery,debugLevel=0,quiet=FALSE)
scenario$iterationCallback <- function(iteration,elites,progress) {
  path <- file.path(run_dir,"iteration_progress.csv")
  if(!file.exists(path)) cat("unix_time,iteration,experiments_used,remaining_budget\n",file=path)
  cat(sprintf("%.3f,%d,%d,%d\n",as.numeric(Sys.time()),iteration,
              progress$experimentsUsed,progress$remainingBudget),file=path,append=TRUE)
  flush.console()
}
if(getarg("smoke","false")=="true") {
  scenario$nbConfigurations <- 8L
  scenario$minNbSurvival <- 2L
  scenario$firstTest <- 2L
  scenario$eachTest <- 1L
  scenario$nbIterations <- 1L
  scenario$postselection <- FALSE
}
if(getarg("check-only","false")=="true") {
  checked <- irace::checkIraceScenario(scenario)
  cat("SCENARIO CHECK PASSED\n")
} else {
  elites <- irace::irace(scenario)
  write.csv(as.data.frame(elites),file.path(run_dir,"elite_configurations_units.csv"),row.names=FALSE)
  cat("CAMPAIGN COMPLETED\n")
}
