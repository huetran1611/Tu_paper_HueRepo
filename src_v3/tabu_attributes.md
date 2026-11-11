# Tabu attributes — cập nhật 11/11/2026

Triển khai trong `paper_neighborhoods.hpp`, được include bởi `tabubu_TimeDependent_v3.cpp`. Áp dụng các loại thuộc tính trong §5.3.4 của paper: relocated customers, exchanged blocks, modified trip edges. Paper không cung cấp bảng khóa cụ thể cho từng operator; bảng dưới đây là cách ánh xạ được lựa chọn trong code.

| Operator | Thuộc tính customer/block |
|---|---|
| N0 relocate (1,0) | Khách được di chuyển |
| N1 exchange (1,1) | Hai khách được trao đổi |
| N2 relocate (2,0) | Cặp khách liên tiếp có thứ tự được di chuyển |
| N3 intra-trip 2-opt | Không thêm customer/block; dùng cạnh |
| N4 inter-trip 2-opt* | Không thêm customer/block; dùng cạnh |
| N5 exchange (2,1)/(1,2) | Cặp có thứ tự và khách đơn được trao đổi |
| N6 exchange (2,2) | Hai cặp có thứ tự được trao đổi |
| N7 depth-2 ejection | Khách u từ A và khách v bị đẩy khỏi B |
| N8 trip fusion | Không thêm customer/block; dùng cạnh |

Mỗi operator có bộ nhớ riêng. Khóa customer/block không chứa chỉ số trip vì trip có thể bị xóa hoặc đổi thứ tự. Khi customer/block đang tabu, các move tiếp tục dùng entity đó trong cùng operator bị cấm, trừ aspiration. Đây là cấm theo entity, rộng hơn chỉ cấm quay lại đúng vị trí cũ. Cặp `[u,v]` có thứ tự khác `[v,u]`.

Ngoài các thuộc tính trên, mọi operator giữ bộ nhớ cạnh có hướng `(vehicle,from,to)`: khi chấp nhận move, các cạnh bị xóa bị cấm khôi phục. Kết hợp cạnh với entity còn chặn được move đảo ngược có mô tả khác, ví dụ đổi thứ tự hai khách kề nhau bằng cách relocate khách còn lại.

Ứng viên hoàn chỉnh vẫn được đánh giá và xét global feasible incumbent **trước** lọc tabu. Một ứng viên bị tabu nếu dùng entity bị cấm hoặc thêm lại cạnh bị cấm. Aspiration cho phép vượt toàn bộ các thuộc tính nếu active score cải thiện Sref; không thêm điều kiện khả thi.

Chỉ khi SA chấp nhận, các thuộc tính của ứng viên được chọn mới được commit. Tất cả thuộc tính của cùng move dùng chung một tenure ngẫu nhiên trong `[tau0,2*tau0]`, với `tau0=ceil(c_tau*sqrt(n))`. Với move chấp nhận tại iteration t và tenure L, cấm ở t+1 tới t+L, hết hạn tại t+L+1. Thuộc tính được dọn khi hết hạn và khi reset tabu/diversification.

Thay đổi này không đổi cách sinh N7: đủ bộ ba xe khác nhau có thứ tự, top3 vị trí chèn trên toàn C theo distance delta; không bổ sung hash lọc trùng ứng viên. SA, reward, penalty và các hàm score không thay đổi.

Kiểm thử bổ sung trong `test_paper_model.cpp`: chỉ dùng entity tabu vẫn chặn được cả sáu operator liên quan; đúng loại và số customer/block; aspiration vượt tabu; selection chưa commit; entity hết hạn cho phép move; edge và entity dùng cùng expiry; clear xóa cả hai loại; relocate/swap đảo ngược bị cấm khi cố ý xóa riêng edge memory để cô lập quy tắc customer.

Snapshot trước sửa: `validation/tabu_attributes_before_20261111/`. Báo cáo `paper_algorithm_audit_20261111.md` mô tả trạng thái trước cập nhật; phát hiện số 3 đã được xử lý theo bảng này. Các phát hiện khác không nằm trong thay đổi này.

Đã chạy `verify_v3.ps1 -Compiler D:/mingw64/bin/g++.exe`: build release/debug thành công; PASS semantic tests và các test tabu mới; PASS seed reproducibility; PASS mode switching/destroy–repair khi stagnation; smoke 10/50 khách và drone-only hoàn tất. Executable production `tabubu_TimeDependent_v3.exe` đã được build lại. Đây là kiểm tra tính đúng chức năng, chưa phải so sánh chất lượng nghiệm trên full benchmark.
