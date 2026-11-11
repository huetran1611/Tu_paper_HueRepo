> C?p nh?t 2026-11-12: ch?nh s?ch ch?y ??ng th?i b?n d??i ?? ???c thay b?ng **m?t batch ho?t ??ng, t?i ?a 20 job song song, t? ??ng chuy?n batch**. Xem [h??ng d?n ?i?u ph?i hi?n t?i](PAPER_TABLES_678_AUTOMATION.md). Timeout job hi?n l? 240 ph?t ?? ch? API; solver v?n t?i ?a 120 ph?t.

# Tái hiện thiết kế Bảng 6, 7, 8 bằng v3 và tham số Bảng 5

Thiết kế cập nhật: **38 workflow × 250 job + 1 workflow × 100 job = 9.600 job**. Mỗi job chạy đúng một solver process. Setup và report là hai workflow riêng, không chiếm job phụ trong 39 workflow thực nghiệm. Các file không tự chạy khi push; không chạy thêm solver trên máy Windows đang tuning irace.

## Cách chạy

1. Đưa tất cả file `.github/workflows/paper-tables-*.yml`, `scripts/paper_tables_678.py`, `scripts/paper_tables_678_actions.py`, `scripts/test_paper_tables_678.py`, `scripts/test_paper_tables_678_actions.py`, `scripts/generate_paper_tables_678_workflows.py` và **hai source v3** (`src_v3/tabubu_TimeDependent_v3.cpp`, `src_v3/paper_neighborhoods.hpp`) lên cùng commit. Giữ hai file data đã version-control: `.github/workload-tiebreak/inputs.zip` và `inputs.json`. Các workflow `workflow_dispatch` cần được GitHub nhận diện trên default branch trước khi dùng nút Run workflow.
2. Chọn **Actions → Paper Tables 6-7-8 - setup or resume 39 workflows → Run workflow**. Lần đầu để trống `setup_run_id`; `launch_batches=true` mặc định. Setup compile, kiểm thử và chụp bundle; sau đó tự dispatch 39 batch workflow bằng GITHUB_TOKEN. Không cần PAT riêng. Nếu chỉ muốn chuẩn bị và xem kế hoạch, đặt launch_batches=false.
3. Ghi lại **run ID của setup đầu tiên**. Artifact `paper-t678-bundle` nằm ở run đó. Từng batch có tên **Paper T678 batch 01…39**; mỗi job giữ request, stdout/stderr, checkpoint, completed.json và CPU time. Không có job build hay tổng hợp bên trong batch.
4. Khi các batch xong, chạy **Paper Tables 6-7-8 - collect 39 workflows**, nhập `setup_run_id` ban đầu. Workflow kiểm kê completion artifact thành công từ mọi lần chạy batch gắn với setup đó, lấy record đã nén trong log của từng workflow rồi tạo artifact `paper-t678-report`: `REPORT.md`, `tables.json`, `runs.csv`, `missing_tasks.json` và inventory. Mỗi completion record được xuất vào log sau khi artifact riêng của job đã upload thành công, kèm artifact ID để đối chiếu chính xác. Cách này chỉ cần khoảng 39 archive log thay vì 9.600 REST download. Có fallback tải riêng tối đa 100 artifact nếu bước xuất log bị ngắt; vượt ngưỡng thì báo lỗi để tránh tiêu hết API quota. Có thể chạy report khi chưa đủ; nó xuất bảng một phần và báo thiếu. Batch còn chạy chưa có log archive hoàn chỉnh sẽ được thu ở lần report sau.
5. Để chạy tiếp: chạy lại workflow **setup or resume**, nhập **setup_run_id ban đầu**, giữ launch_batches=true. Không dùng ID của một batch hoặc report. Setup dùng lại bundle nguyên bản, liệt kê artifact `-ok` còn hạn và chỉ dispatch các slot chưa hoàn tất; batch đang queued/running không bị dispatch trùng. Batch đã đủ kết quả không tạo workflow mới. Lượt lỗi hoặc chưa upload completion artifact chạy lại từ đầu. Chỉ completion artifact đã upload thành công mới có thể được dùng để bỏ qua lượt đó.

Có thể chạy riêng một batch qua nút Run workflow của batch, nhập setup_run_id. Trường `slots` là mảng JSON các số task cần chạy; mặc định có 250 slot ở batch 01–38 và 100 ở batch 39. Để tự động chọn đúng lượt thiếu, ưu tiên dùng setup/resume thay vì giữ mặc định chạy lại cả batch.

### Các file chính

- Setup/resume: `.github/workflows/paper-tables-6-7-8-v3.yml`.
- Batch: `.github/workflows/paper-tables-678-batch-01.yml` tới `paper-tables-678-batch-39.yml`.
- Worker dùng chung: `.github/workflows/paper-tables-678-worker.yml` (workflow_call; một matrix job, một solver cho mỗi phần tử).
- Report: `.github/workflows/paper-tables-678-report.yml`.
- Generator tạo lại đúng 39 file entry: `python scripts/generate_paper_tables_678_workflows.py`; kiểm tra đồng bộ bằng `--check`.

Setup dùng `actions:write` để dispatch workflow; các batch và report chỉ cần `actions:read`. Tài khoản/tổ chức vẫn phải có quota Actions và cho phép các action được dùng. Workflow không tự push code, thay branch hay sửa dữ liệu.

## Dữ liệu và ngân sách

- 96 synthetic **test** instances của §6.3: n={6,10,12,20} × grid={5,10,20} × 4 = 48; n={50,100,200} × grid={10,20,30,40} × 4 = 48. Không có n500, Hanoi hay instance training irace.
- Dùng archive input đã có của repo, kiểm tra SHA256 từng file. Đã đối chiếu 96 file instance trong archive với `instance_time_dependent` hiện tại: nội dung token giống nhau; khác byte do xuống dòng. Demand thuộc (0,2], service=60, W∈{1800,2700,3600}. Vmax/theta từ sidecar của từng instance, không dùng profile mặc định để thay thế.
- Fleet lấy từ header, không tự ép về (1,1)/(2,2).
- 10 seed 42–51, một attempt/tiến trình; đây là lựa chọn tái lập vì paper chỉ nêu 10 lượt, không nêu seed cụ thể.
- Budget cơ sở B=9*n*ceil(sqrt(n)); doubled controls dùng 2B. Segment=ceil(2*n), giữ nguyên khi nhân đôi budget.
- Hard wall cap chung 7.200 giây/tiến trình, gồm init. Solver dừng hợp tác ở 7.198 giây để kịp ghi checkpoint; watchdog tối đa 7.200 giây. Quy tắc này giữ yêu cầu n200 không quá 2 giờ. **Job bị cap không được tính thành lượt full-budget** trong bảng, phải được liệt kê thiếu; do đó cap không âm thầm thay đổi ý nghĩa so sánh doubled budget.
- Manifest v2 ánh xạ duy nhất mỗi lượt sang (batch,slot): batch 01–38 có 250 lượt, batch 39 có 100 lượt. Mỗi matrix job chỉ gọi `task` một lần; không có vòng lặp chạy nhiều solver trong một job. Timeout job 150 phút gồm download/upload; solver vẫn tối đa 120 phút.
- **Không đặt max-parallel** cho matrix. GitHub điều phối số job thực tế theo quota concurrency của tài khoản/tổ chức, runner sẵn có và các workflow khác. Tách 39 workflow không vượt qua giới hạn toàn tài khoản và không bảo đảm 9.600 job cùng chạy một lúc. Concurrency group chỉ ngăn hai lần chạy của cùng batch/setup chồng nhau; không khóa các batch khác.
- Bundle v1 kiểu 240 shard trước đây không tương thích kế hoạch mới; tạo setup mới cho thiết kế 39 batch. Không sửa hay trộn artifact v1 đã chạy.

## Cấu hình đối chứng

| ID | Selection | Đổi mode | SA | DR | Workload tie-break | Budget |
|---|---|---|---|---|---|---|
| cyclic | Cyclic | Tắt | Tắt | Tắt | Tắt | B |
| random | Uniform random | Tắt | Tắt | Tắt | Tắt | B |
| adaptive_mk | Adaptive | Tắt | Tắt | Tắt | Tắt | B |
| switch | Adaptive | Bật | Tắt | Tắt | Tắt | B |
| mk_tie | Adaptive | Tắt | Tắt | Tắt | Bật | B |
| mk_tie_2x | Adaptive | Tắt | Tắt | Tắt | Bật | 2B |
| mk_2x | Adaptive | Tắt | Tắt | Tắt | Tắt | 2B |
| switch_dr | Adaptive | Bật | Tắt | Bật | Tắt | B |
| switch_sa | Adaptive | Bật | Bật | Tắt | Tắt | B |
| switch_dr_sa | Adaptive | Bật | Bật | Bật | Tắt | B |

- Bảng 6: baseline **cyclic**, so random và adaptive_mk.
- Bảng 7: baseline **adaptive_mk**, so switch, mk_tie, mk_tie_2x, mk_2x.
- Bảng 8: baseline **switch**, so switch_dr, switch_sa, switch_dr_sa.
- Chia sẻ baseline hợp lệ giữa các bảng: tổng **10 cấu hình × 96 instance × 10 seed = 9.600 lượt**, thay vì chạy lặp lại baseline.
- Cyclic đúng thứ tự §6.3.1: N0,N1,N2,N5,N6,N3,N4,N7,N8. Không dùng đơn giản N0…N8.
- Khi SA tắt, nhận strict improvement; với tie-break có thể nhận cải thiện workload khi primary score bằng nhau. Khi bật, SA giữ relative delta và lịch nhiệt toàn cục của v3.
- Tie-break dùng active penalized makespan làm primary, workload L2 thuần làm secondary khi primary bằng nhau; không cộng epsilon*workload vào objective. Reward, Sref và aspiration dùng cùng thứ tự từ điển. Với nghiệm khả thi, thứ tự đúng `(Cmax, ||C||2)` của paper; paper chưa mô tả chi tiết cách mở rộng tie-break sang nghiệm vi phạm, nên đây là lựa chọn triển khai được ghi rõ.
- Makespan-only giữ nguồn N0–N6 ở critical vehicles; N7/N8 toàn fleet. Mode workload dùng toàn fleet như v3.

## Tham số Bảng 5

Giữ cố định cseg=2, c_tabu=2, Hmode=1, Hdiv=4, gamma=(0.70,0.30,0.10,0.30), T0=0.005, alpha=0.9982, kappa=1, tau_v=0.005, r_destroy=0.20, eta=1. Truck capacity=40 kg; drone capacity=2.27 kg; các tham số drone còn lại lấy từ v3/Bảng 4. Không đọc elite configuration từ campaign irace đang chạy.

## Cách tổng hợp

Mỗi cặp (instance, configuration) cần đủ 10 seed khả thi và full budget. Tính best makespan, mean makespan và mean **CPU time của riêng tiến trình solver**, rồi tính gap so baseline **trên từng instance**:

`100 * (metric_variant / metric_baseline - 1)`.

Sau đó lấy trung bình không trọng số của 96 gap. Không lấy ratio giữa hai trung bình toàn dataset. CPU time đo user+system bằng `resource.getrusage(RUSAGE_CHILDREN)` trên Linux, không dùng wall time khi chạy song song để thay CPU time. Wall time vẫn được lưu để tham khảo.

Report ghi rõ số instance đủ cặp/96 cho mỗi dòng; nếu chưa đủ 9.600 lượt thì workflow report báo thiếu và upload kết quả một phần, không coi đó là bảng hoàn chỉnh. Baseline CPU time bằng 0 không được chia để tạo gap giả. Trường hợp chạy thủ công lặp một slot, report chỉ chọn completion artifact thành công mới nhất còn hạn cho slot đó, đối chiếu artifact ID của record trong log rồi xác nhận task/protocol khớp bundle. Cần giữ cả log workflow và artifact tới khi tổng hợp; artifact được đặt retention 90 ngày, còn thời hạn log chịu thiết lập repository.

## Phạm vi tái hiện và kiểm chứng

Đây là tái hiện **thiết kế thực nghiệm** trên v3 hiện tại, không cam kết ra đúng các con số in trong paper. N7 dùng ngoại lệ đã được người dùng chốt: đủ bộ ba xe phân biệt có thứ tự và top3 gap trên C theo delta khoảng cách; tabu dùng bản customer/block + edge mới. RNG, các sai khác dữ liệu đã ghi ở `src_v3/dataset_audit.md`, CPU/compiler và chi tiết init cũng có thể làm kết quả khác paper. GitHub Ubuntu runner không đảm bảo đúng AMD EPYC 7763 của paper.

Adapter tạo source ablation riêng trong artifact và fail nếu các điểm móc trong source gốc thay đổi. Không sửa cpp/header/executable production, không thay artifact hoặc tham số campaign irace đang chạy.

Kiểm tra trước khi bàn giao: compile adapter trên MinGW, chạy thật cả 10 ablation trên instance 6.5.1, kiểm tra flags/telemetry, full/doubled budget, Table-5 values, cyclic order và lexicographic comparator; kiểm tra công thức tổng hợp bằng oracle có baseline khác scale và bắt lỗi missing coverage. Test bổ sung kiểm tra 39 workflow phủ đúng 9.600 slot không trùng; mỗi job chỉ gọi một solver; bỏ qua artifact lỗi/hết hạn/khác campaign; resume chỉ gửi slot thiếu và không dispatch batch đang chạy. Các tương tác GitHub API được mock trong test local; chưa dispatch workflow thật. Setup mới chạy lại test trên Ubuntu trước khi dispatch batch. Đợt kiểm tra local không chạy toàn bộ 9.600 lượt.

Tài liệu GitHub về [matrix và giới hạn 256 job](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [workflow_dispatch bằng GITHUB_TOKEN](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow), [artifact API](https://docs.github.com/en/rest/actions/artifacts) và [download-artifact](https://github.com/actions/download-artifact) là cơ sở cho thiết kế batch/resume.
