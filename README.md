# UI Spec Checker

Tool so sánh **screenshot bản đã code** với **bản thiết kế** (ảnh upload hoặc link Figma) để tìm chỗ lệch spec:

| Nhóm | Kiểm tra |
| --- | --- |
| Khoảng cách | khoảng cách dọc/ngang giữa các phần tử, khoảng cách dòng (line-height), padding trong khối, lề màn hình, căn giữa |
| Font chữ | cỡ chữ, độ đậm (font-weight), font-family / letter-spacing |
| Màu sắc | màu chữ, màu nền khối, màu viền (ΔE CIEDE2000) |
| Kích thước | chiều cao / chiều rộng của khối (button, card, input…) |

Tool **không** kiểm tra nội dung chữ hay hình ảnh: text chỉ dùng làm "mốc" để ghép phần tử của design với phần tử của bản code, icon/ảnh chỉ được so vị trí và kích thước.

## Chạy nhanh

### Docker

```bash
docker build -t uicheck .
docker run -p 8000:8000 -e FIGMA_TOKEN=figd_xxx uicheck   # FIGMA_TOKEN là tuỳ chọn
```

Mở http://localhost:8000.

### Chạy trực tiếp

Cần Python 3.10+ và [Tesseract OCR](https://tesseract-ocr.github.io/tessdoc/Installation.html) kèm gói tiếng Việt:

```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-vie
# macOS
brew install tesseract tesseract-lang

pip install -r requirements.txt
uvicorn uicheck.server:app --port 8000
```

## Cách dùng (giao diện web)

1. **Design**: dán link Figma tới frame (chuột phải frame → *Copy link to selection*) kèm [Figma personal access token](https://help.figma.com/hc/en-us/articles/8085703771159), hoặc chuyển sang tab *Upload ảnh*.
2. **Bản code**: kéo thả / dán (Ctrl+V) screenshot của màn hình tương ứng.
3. Bấm **So sánh**. Kết quả gồm:
   - tổng số vấn đề theo nhóm, mức *Nghiêm trọng* / *Nhẹ*;
   - 2 ảnh đặt song song, đánh số và tô vùng bị lệch (vùng tô màu = khoảng cách được đo);
   - chế độ *Chồng lớp* (chỉnh độ mờ, chế độ difference) để nhìn độ trôi;
   - danh sách chi tiết: giá trị design, giá trị bản code, chênh lệch. Bấm vào một dòng để nhảy tới vị trí trên ảnh;
   - nút *Tải JSON* để lưu báo cáo.

Khi dùng link Figma, tool lấy thêm thông số gốc của layer (font family, weight, size, màu fill, tên layer) để hiển thị giá trị mong đợi chính xác.

### Mẹo để kết quả chính xác

- Dùng **PNG**, export design ở **@2x/@3x**. Ảnh JPEG hoặc 1x vẫn chạy được nhưng tool phải nới ngưỡng màu/độ đậm.
- Hai ảnh phải là **cùng một màn hình, cùng trạng thái** (cùng dữ liệu càng tốt).
- Scale được tự đoán từ chiều rộng ảnh (1170px → @3x của màn 390px, 750px → @2x của 375px…). Nếu đoán sai, chọn tay trong *Tuỳ chọn nâng cao*.
- Status bar / home indicator khác nhau giữa máy thật và design: đặt *Bỏ qua phía trên/dưới* (vd. 44px / 34px).
- Ngưỡng mặc định: khoảng cách ±2px, cỡ chữ ±1px, màu ΔE ≤ 3 (ΔE < 2 gần như không nhìn ra, > 5 là khác màu rõ).

## CLI (dùng được trong CI)

```bash
python -m uicheck --dev screenshot.png --design design.png --json report.json
python -m uicheck --dev screenshot.png --figma "https://www.figma.com/design/KEY/App?node-id=12-345" --token figd_xxx
python -m uicheck --help   # tất cả tuỳ chọn: --tol-spacing, --tol-color, --checks, --ignore-top, ...
```

Exit code: `0` không có vấn đề, `1` chỉ có vấn đề nhẹ, `2` có vấn đề nghiêm trọng, `3` lỗi đầu vào.

Thử ngay với ảnh mẫu: `python -m uicheck --dev examples/dev.png --design examples/design.png`.

## API

`POST /api/compare` (multipart/form-data)

| Trường | Mô tả |
| --- | --- |
| `dev` | file screenshot bản code (bắt buộc) |
| `design` | file ảnh design (khi không dùng Figma) |
| `figma_url`, `figma_token` | link frame Figma và token (token có thể đặt qua biến môi trường `FIGMA_TOKEN`) |
| `design_scale`, `dev_scale` | `auto` hoặc pixel ratio (1, 2, 3…) |
| `tol_spacing`, `tol_font_size`, `tol_color` | ngưỡng sai số |
| `checks` | `spacing,padding,font,color,size` |
| `ignore_top`, `ignore_bottom` | số px bỏ qua ở trên/dưới |
| `lang` | ngôn ngữ OCR: `vie+eng` (mặc định) hoặc `eng` |

Kết quả trả về JSON: `issues` (mỗi vấn đề có `category`, `label`, `expected`, `actual`, `delta`, `severity`, toạ độ đánh dấu), `summary`, `elements` (các phần tử phát hiện được), `warnings`.

## Cách hoạt động

1. **Chuẩn hoá**: đưa 2 ảnh về cùng tỉ lệ (3 px ảnh / 1 px thiết kế với mobile, 2 với desktop).
2. **Phát hiện phần tử** (chạy giống hệt nhau trên cả 2 ảnh, nên sai số đo triệt tiêu nhau):
   - *Khối*: vùng màu phẳng có nền khác nền cha hoặc có viền; mép khối được tinh chỉnh tới mức subpixel.
   - *Text*: OCR (Tesseract) trên ảnh đã chuẩn hoá tương phản (chữ trắng trên nút màu cũng đọc được), rồi đo khung mực chữ, màu lõi nét chữ và độ dày nét.
   - *Icon/ảnh*: phần nội dung còn lại trong mỗi khối.
3. **Ghép cặp**: text ghép theo nội dung + vị trí; khối và icon ghép theo vị trí (đã bù độ lệch cục bộ) và text bên trong.
4. **So sánh**: khoảng cách giữa các phần tử cùng cấp, padding (từ mép khối tới nội dung), lề màn hình, cỡ chữ (theo chiều cao chữ), độ đậm (độ dày nét), font-family (cùng nội dung + cỡ chữ mà chiều rộng/hình dạng khác), màu (ΔE2000). Lỗi "dây chuyền" được lọc bớt (vd. card tăng padding thì input bên trong hẹp lại — chỉ báo padding).

### Đọc kết quả

- Khoảng cách là **khoảng cách nhìn thấy**, đo tới mép mực của chữ (không tính phần line-height trống), nên con số có thể khác giá trị CSS/Figma vài px — **cột chênh lệch** mới là thứ cần sửa.
- Cỡ chữ có dấu `≈` là ước lượng từ ảnh; khi dùng Figma, giá trị design là giá trị gốc.
- Font-family không thể đọc chính xác từ ảnh: tool báo khi chữ cùng nội dung, cùng cỡ nhưng rộng hơn/hẹp hơn hoặc khác hình dạng.

## Giới hạn

- Màn hình có nhiều ảnh/gradient phức tạp sẽ có ít phần tử được đối chiếu hơn (tool chỉ báo những gì ghép được chắc chắn).
- Nếu nội dung động khác nhau (tên người dùng, số liệu…) tool vẫn ghép theo vị trí nhưng bỏ qua kiểm tra font cho các text đó.
- Shadow, border-radius, opacity chưa được kiểm tra.

## Phát triển

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Test dựng các màn hình giả lập (`tests/synth.py`) rồi cố tình làm lệch padding, khoảng cách, cỡ chữ, độ đậm, font, màu, viền… và kiểm tra tool phát hiện đúng, không báo nhầm khi 2 ảnh giống nhau (kể cả khác scale, khác chiều rộng màn hình, ảnh JPEG).
