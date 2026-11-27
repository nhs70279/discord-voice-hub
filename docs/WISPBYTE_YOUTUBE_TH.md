# ทดลองแก้ YouTube ขอให้ยืนยันตัวตนบน Wispbyte Premium

วิธีนี้เป็นการทดลองตามคำแนะนำจากฝ่ายสนับสนุน Wispbyte: ใช้ **PO Token Provider** ร่วมกับ `cookies.txt` จาก **บัญชี YouTube สำรองที่เจ้าของยินยอมให้ใช้กับบอท** ไม่รับประกันว่าจะผ่านการตรวจสอบของ YouTube ทุกครั้ง เพราะข้อจำกัดจาก IP ของศูนย์ข้อมูลยังเกิดขึ้นได้

## สิ่งที่โค้ดเตรียมไว้แล้ว

- เพิ่มปลั๊กอิน `bgutil-ytdlp-pot-provider==2.0.2` สำหรับ `yt-dlp`
- เปิด PO Token Provider เฉพาะเมื่อกำหนด `YOUTUBE_POT_SERVER_HOME` **หรือ** สร้างไฟล์เปิดใช้งาน `data/youtube-pot.enabled` และเซิร์ฟเวอร์ย่อยที่รันบน `127.0.0.1:4416` เริ่มทำงานได้จริง
- เมื่อ Provider พร้อม จะลอง YouTube แบบ `mweb` ก่อน และมีตัวเลือกสำรองแบบเดิม
- ถ้าไม่ได้ตั้งค่า Provider หรือเปิดไม่ได้ บอทใช้วิธี `web_safari` ตามเดิมโดยไม่หยุด Discord หรือระบบอ่านข้อความ
- ไม่ส่ง Bot Token, Discord OAuth Secret หรือเส้นทางไฟล์ Cookie ไปให้โปรเซส Provider

## 1. ติดตั้งเซิร์ฟเวอร์ย่อยของ PO Token (ทำครั้งเดียว)

### วิธีอัตโนมัติสำหรับ Wispbyte Premium

1. ใน Wispbyte Premium → **Files** → โฟลเดอร์ `data` → **File** ให้สร้างไฟล์ว่างชื่อ `youtube-pot.enabled` (เป็นสวิตช์เปิดใช้งาน ไม่ใช่ Cookie)
2. ใน Wispbyte → **GitHub** → `my-server` → **Deploy** เพื่อดึงโค้ดใหม่และรีสตาร์ตหนึ่งครั้ง
3. ก่อนเริ่มบอท สคริปต์ `.wispbyte/deploy.sh` จะติดตั้ง PO Token Server รุ่น `2.0.2` เฉพาะครั้งแรก โดยใช้ Deno ที่มีอยู่ใน `.runtime` และตรวจเวอร์ชัน Git tag ให้ตรงกัน
4. หากติดตั้งไม่สำเร็จ จะกลับไปใช้ YouTube แบบเดิมและให้ Discord Bot เริ่มทำงานต่อ การติดตั้งซ้ำจะเกิดขึ้นเฉพาะเมื่อยังไม่มีไฟล์ยืนยันว่าติดตั้งสำเร็จ

ขั้นตอนนี้อาจใช้เวลาและ RAM มาก จึงควรตรวจสถานะหลัง Deploy ก่อนสั่งเล่นเพลง การย้อนกลับคือปิดสวิตช์ `data/youtube-pot.enabled` แล้วรีสตาร์ต โดยไม่ลบฐานข้อมูลหรือเพลย์ลิสต์

### วิธีติดตั้งด้วย Shell สำหรับเซิร์ฟเวอร์ที่มี SSH จริง

ใน Shell ของเซิร์ฟเวอร์ที่มี SSH ให้หยุดบอทก่อนการติดตั้งหากหน่วยความจำเหลือน้อย จากนั้นรันคำสั่งในไดเรกทอรีโปรเจกต์ **ช่อง Console ปกติของ Wispbyte ไม่ใช่ Shell สำหรับรันคำสั่งเหล่านี้**:

```bash
mkdir -p data
git clone --depth 1 --single-branch --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git data/bgutil-ytdlp-pot-provider
cd data/bgutil-ytdlp-pot-provider/server
deno install --allow-scripts=npm:canvas --frozen
```

ต้องมี `git` และ `deno >= 2.0` ในเครื่อง หาก `deno` อยู่ใน `.runtime/deno` ให้เรียกไฟล์นั้นแทน ตัวติดตั้ง JS อาจใช้ RAM/CPU มากและอาจไม่สำเร็จในแพ็กเกจเล็ก **อย่าติดตั้งซ้ำทุกครั้งที่บอทเริ่มทำงาน**

กำหนดในตัวแปรสภาพแวดล้อมของ Wispbyte (หรือ `.env` ที่ไม่อยู่ใน Git):

```dotenv
YOUTUBE_POT_SERVER_HOME=data/bgutil-ytdlp-pot-provider/server
```

จะใช้เส้นทางแบบ absolute ก็ได้ **ห้ามเปิดพอร์ต 4416 ให้เข้าจากภายนอก** ตัวบอทจะเปิด Provider ในเครื่องและปิดตามเมื่อหยุดบอท

## 2. Cookie จากบัญชีสำรอง (ถ้าระบบยังขึ้น Sign in to confirm)

1. เจ้าของบัญชีสร้าง/เลือกบัญชี YouTube สำรองสำหรับการทดสอบนี้ โดยตระหนักว่าการใช้ Cookie กับเครื่องมือดาวน์โหลดอาจทำให้บัญชีถูกจำกัดหรือระงับ
2. บนเบราว์เซอร์ของเจ้าของบัญชี ให้ล็อกอิน YouTube ในหน้าต่างส่วนตัว แล้วส่งออก Cookie รูปแบบ **Netscape `cookies.txt`** ตามคู่มือ `yt-dlp` ของทางการ ห้ามใช้บัญชีหลักหรือดึง Cookie จากอุปกรณ์ของผู้อื่น
3. อัปโหลดไฟล์ไปที่ `data/youtube-cookies.txt` ใน **Files ของ Wispbyte Premium เท่านั้น** ห้ามส่งในแชต อีเมล หรือ GitHub
4. ตั้งสิทธิ์ไฟล์ให้อ่านได้เฉพาะเจ้าของ:

   ```bash
   chmod 600 data/youtube-cookies.txt
   ```

5. ตั้งค่าต่อไปนี้ใน Wispbyte (หรือ `.env` ส่วนตัว):

   ```dotenv
   YOUTUBE_COOKIES_PATH=data/youtube-cookies.txt
   ```

ไฟล์ Cookie เป็นข้อมูลล็อกอินที่มีความอ่อนไหวสูง หากทำหายหรือรั่วไหล ต้องออกจากระบบและยกเลิกเซสชันของบัญชีนั้น เมื่อไม่จำเป็นต้องใช้แล้วให้เอาค่า `YOUTUBE_COOKIES_PATH` ออก และลบไฟล์ด้วยตนเอง

## 3. ตรวจผลโดยไม่กระทบระบบอ่านข้อความ

1. รีสตาร์ต Wispbyte Premium เพียงครั้งเดียวและตรวจ Console ว่ามี `YouTube PO token provider ready on localhost` หรือไม่
2. หากไม่พบ ให้ตรวจความสมบูรณ์ของการติดตั้ง JS ก่อน อย่าเพิ่งสรุปว่า YouTube บล็อก IP
3. ทดลอง `/play` ด้วยวิดีโอ YouTube ที่เป็นสาธารณะหนึ่งเพลง และตรวจว่าได้ยินในห้องเสียงจริง
4. หากยังขึ้น `Sign in to confirm you're not a bot` แม้ Provider ทำงานและ Cookie ใช้ได้ ให้บันทึก **เฉพาะข้อความผิดพลาดที่ตัด Token/Cookie ทิ้งแล้ว** เพื่อตรวจต่อ
5. ดู CPU และ RAM ระหว่างเล่นเพลง หากกระทบการอ่านข้อความ ให้ลบค่า `YOUTUBE_POT_SERVER_HOME` แล้วรีสตาร์ตเพื่อกลับไปใช้เส้นทางเดิมทันที

อ่านเพิ่ม:

- [yt-dlp: PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide)
- [bgutil PO Token Provider: วิธีติดตั้งและตรวจสอบ](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- [yt-dlp: Export Cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
