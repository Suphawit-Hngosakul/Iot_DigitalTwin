# cloud/presign (issue #21)

`POST /presign` + token + ชื่อไฟล์ → คืน presigned PUT URL อายุ 5 นาที
บังคับ path ให้อยู่ใต้ `raw/audio/device_id=<เครื่องนั้น>/` เท่านั้น
