# Tuning `tabubu_TimeDependent_v2.cpp` with irace

Các file trong thư mục này tạo một môi trường test tối thiểu cho `irace`.

## 1. Cài irace trong môi trường base

```bash
conda activate base
mkdir -p irace/Rlib
Rscript -e '.libPaths(c("irace/Rlib", .libPaths())); install.packages("irace", lib="irace/Rlib", repos="https://cloud.r-project.org")'
```

Kiểm tra:

```bash
Rscript -e '.libPaths(c("irace/Rlib", .libPaths())); library(irace); packageVersion("irace")'
```

## 2. Build thuật toán

```bash
mkdir -p build
g++ -O3 -std=c++20 src_v2/tabubu_TimeDependent_v2.cpp -o build/tabubu_TimeDependent_v2
```

## 3. Smoke test runner

```bash
chmod +x irace/target-runner.sh
./irace/target-runner.sh 1 1 123 instance_time_dependent/6.5.1.txt 0 \
  --iters 100 --segment-iters 20 --no-improve 80 --knn-k 5 --knn-window 1 --alpha 0.999
```

Runner phải in ra đúng một số, ví dụ `889.699124`. Số càng nhỏ càng tốt.

## 4. Chạy irace

```bash
Rscript -e '.libPaths(c("irace/Rlib", .libPaths())); irace::irace(cmdline="--scenario irace/scenario.txt")'
```

Kết quả được lưu ở `irace/irace.Rdata`, log từng lần chạy ở `irace/logs/`.

## 5. Xem cấu hình tốt nhất

```bash
Rscript -e '.libPaths(c("irace/Rlib", .libPaths())); load("irace/irace.Rdata"); print(iraceResults$allConfigurations[iraceResults$iterationElites, ])'
```

## Gợi ý tăng ngân sách

Trong `irace/scenario.txt`, tăng `maxExperiments` từ `200` lên `1000` hoặc hơn khi đã chắc runner chạy ổn. Nếu máy mạnh, tăng `parallel`.
