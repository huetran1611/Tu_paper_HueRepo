# V4 theo paper (4), bắt đầu từ snapshot v3 đã tuning

Nguồn gốc là hai file C++/header trong
`experiments/irace_v3_log/runs/log15000_884_20261112/artifacts`.
Không lấy một bản v3 đã chỉnh tiếp sau tuning. SHA256 của nguồn gốc nằm trong
`provenance.json`. V3, executable và các kết quả irace cũ được giữ nguyên.

## Thành phần đã sửa

| Thành phần | V3 đã tuning | V4 |
|---|---|---|
| Vi phạm, (23)–(25) | Chia tổng relative excess cho số trip/khách | Cộng tổng relative excess của từng ràng buộc, kể cả khi xét nghiệm partial; đồng bộ tính đầy đủ và cache |
| Workload, (28)–(31) | L2 của thời gian hoàn tất các xe | Log-sum-exp H_tau, tính ổn định bằng cách trừ Cmax trước exp; fwl=H_tau*Pi |
| Tham số làm trơn | Không có rho | rho mặc định 0,1; tau=rho*Cmax(S_sw)/ln(m), chốt lúc vào mode workload và giữ suốt segment; xe không dùng cũng có mặt trong tổng |
| SA, §5.3.5 | T=T0*alpha^t | T=T0*alpha^(t/ITmax); alpha là tỷ lệ cuối/đầu; không reset khi chuyển mode, segment hay DR |
| Cập nhật phạt, (32) | lambda*=exp[kappa*(avg-tau_v)] | lambda*=exp[kappa*(avg-tau_v)/(avg+tau_v)] |
| Eligibility | Có thể đổi khách drone thành truck-only do vi phạm capacity/energy/deadline của chuyến đơn | Giữ nguyên nhãn đầu vào; init vẫn kiểm tra khả thi, search xử lý vi phạm mềm bằng penalty |
| Chọn đồng điểm | rand()%count, có thiên lệch với tập lớn trên Windows | Reservoir sampling bằng mt19937_64 và uniform_int_distribution; áp dụng construction, fallback, neighbourhood, removal, tenure |
| Fallback construction | Ưu tiên thêm violation/makespan khi cùng penalized score | Chọn đều giữa ứng viên cùng điểm penalized makespan |
| Nhiều attempts | Init lượt sau có thể thấy penalty cuối lượt trước | Reset penalty trước cả init; seed tìm kiếm độc lập theo attempt |
| Tham số mặc định | Bảng 5 của bản cũ | Bảng 5 của paper (4); gamma1=1; có CLI h-gap, psi2, psi3 và kiểm tra quan hệ các reward |
| Tải truck synthetic | 40 kg | 70 kg; Hanoi dùng --truck-capacity=40 |

Trong Appendix B của paper (4), **eta vẫn tồn tại và được cố định bằng 1**.
Eta là phạt khác cụm trong construction; rho là tham số làm trơn workload.
Đây là hai tham số khác nhau. V4 từ chối `--eta=0` thay vì âm thầm dùng lại bộ cũ.

## Thành phần được giữ

- Mô hình thời gian truck phụ thuộc cạnh/khung giờ, tích phân qua các ranh giới
  thời gian; quãng đường truck/drone độc lập và nạp sidecar như v3.
- Tải pickup tăng dần, thời gian phục vụ, thời hạn từ hoàn tất pickup đến về depot,
  năng lượng drone bao gồm đường về, đầy pin và reset tải sau mỗi trip.
- Construction k-means, truck/drone builders, chọn builder rảnh sớm, gán lại drone
  trips giảm dần duration cho drone rảnh sớm, fallback insertion.
- N0–N6 và N8; N0–N6 lấy nguồn từ mọi xe critical ở mode makespan, toàn fleet ở
  mode workload; N7/N8 toàn fleet.
- Tabu theo operator với customer/block và cạnh; commit khi move được chấp nhận,
  tenure uniform [ceil(c_tau*sqrt(n)),2*ceil(c_tau*sqrt(n))], aspiration theo S_ref.
- Cập nhật feasible incumbent theo makespan trước tabu/acceptance, không dùng H_tau
  để chọn incumbent báo cáo.
- Roulette, rewards, cập nhật weights cuối segment, mode workload kéo dài một segment,
  stagnation theo feasible incumbent và thứ tự kích hoạt destroy–repair.
- Worst removal cập nhật critical vehicles sau mỗi lần bỏ khách; repair đảo ngẫu nhiên
  thứ tự khách rồi xét mọi vị trí eligible bằng penalized makespan.
- Cache định tuyến/prefix, các kiểm tra integrity, JSON incumbent, process time cap,
  đường đọc dữ liệu, và ngân sách 9*n*ceil(sqrt(n)). Cache lưu metrics vật lý, còn điểm
  được tính lại theo penalty và tau hiện tại.

## Ngoại lệ và giới hạn cần biết

**N7 giữ ngoại lệ đã được người dùng chốt:** đủ các bộ ba xe vật lý phân biệt có thứ
tự và top 3 vị trí chèn trên xe C; không thêm hash chống trùng toàn nghiệm. Đây là
giới hạn ứng viên của bản triển khai, không được tuyên bố là duyệt đầy đủ mọi vị trí
N7 như một diễn giải exhaustive của paper.

Giữ numerical tolerance và chặn lambda trong [1e-12,1e12] để tránh tràn số; đây là
bảo vệ số học của implementation. Paper xét m>=2; code xử lý bổ sung m=1 bằng
H_tau=Cmax. Các alias CLI/hàm có tên L2 được giữ để tương thích, nhưng đều dùng
H_tau trong v4.

**Không tự áp bộ #1987 cho v4.** Workload, violation, penalty update và ý nghĩa alpha
đã thay đổi. V4 dùng giá trị selected trong Bảng 5 mới, lưu tại
`paper4_defaults.json`; chưa có kết quả tuning riêng cho v4.

## Build và kiểm tra

```powershell
& 'D:/installed/python.exe' src_v4/verify_v4.py
```

File chính: `tabubu_TimeDependent_v4.cpp`; header: `paper_neighborhoods.hpp`.
Script trên build executable và chạy kiểm tra semantic/model, SA endpoints theo
nhiều ngân sách, H_tau và bound, tau cố định, penalty update có giới hạn, summed
violations, eligibility, chọn đều với 100.000 ứng viên đồng điểm, chín neighbourhood
ở hai mode, cache/repair/tabu/aspiration, smoke 10/50 khách, seed reproducibility,
CLI scaling/ratios, failure handling và stagnation/DR.

Log và kết quả kiểm tra: `validation/verification.json` và các thư mục con.
Các kiểm tra ngắn này xác minh hành vi cài đặt; không phải kết quả thực nghiệm
đầy đủ của Bảng 6–8.

Ví dụ chạy với dữ liệu synthetic và tham số mặc định mới:

```powershell
& ./src_v4/tabubu_TimeDependent_v4.exe ./instance_time_dependent/10.10.1.txt `
  --attempts=1 --seed=42 `
  --truck-vmax-file=./instance_time_dependent/10.10.1.vmax_ij.txt `
  --truck-theta-file=./instance_time_dependent/10.10.1.theta_ijl.txt `
  --result-json=./src_v4/validation/example.json
```
