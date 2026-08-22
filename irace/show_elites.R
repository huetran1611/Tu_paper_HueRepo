args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: conda run -n base Rscript irace/show_elites.R irace/rounds/round1/irace.Rdata")
}

load(args[[1]])

if (!exists("iraceResults")) {
  stop("iraceResults not found in ", args[[1]])
}

print(iraceResults$allConfigurations[iraceResults$iterationElites, , drop = FALSE])

