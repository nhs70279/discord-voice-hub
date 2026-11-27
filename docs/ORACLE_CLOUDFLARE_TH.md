# ติดตั้ง Voice Hub ฟรี: Oracle Cloud Always Free + Cloudflare Tunnel

คู่มือนี้ใช้กับ **Ubuntu 24.04 ARM64 บน Oracle Ampere A1** และโค้ดในชุดนี้ โดยรัน **Bot Discord + FastAPI Dashboard ในโปรเซสเดียว** ผ่าน `systemd` ส่วน Cloudflare Tunnel เปิดเฉพาะหน้าเว็บให้เข้าได้ด้วย HTTPS

> สถานะ: เป็นชุดเตรียมติดตั้ง ยังไม่ได้ใช้บัญชี Oracle/Discord/Cloudflare ของคุณทดสอบเสียงจริง ค่าใช้จ่ายเป็นศูนย์ได้ **เมื่ออยู่ในโควตา Always Free** และคุณมีโดเมนที่จัดการ DNS ผ่าน Cloudflare อยู่แล้ว การซื้อโดเมนใหม่อาจมีค่าใช้จ่าย

## 1) เตรียมสิ่งที่ต้องใช้

- บัญชี [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) (อาจต้องยืนยันบัตรและมีข้อจำกัดเรื่อง VM ว่าง)
- บัญชี [Cloudflare Dashboard](https://dash.cloudflare.com/) และโดเมนที่อยู่บน Cloudflare สำหรับชื่อเช่น `bot.example.com`
- บัญชี [Discord Developer Portal](https://discord.com/developers/applications) และสิทธิ์เชิญ Bot เข้าดิสคอร์ดเซิร์ฟเวอร์
- SSH key ส่วนตัวที่เก็บไว้บนคอมพิวเตอร์ของคุณ ไม่ใส่ลงใน ZIP หรือฝากไว้ใน GitHub

Oracle Always Free Ampere A1 ให้โควตา 2 OCPU/12 GB RAM รวมทุก A1 VM ในบัญชี Always Free ปัจจุบัน สามารถเริ่มที่ **1 OCPU/6 GB RAM** ได้ หากเลือก VM ไม่สำเร็จเพราะเครื่องเต็ม ให้รอหรือเลือก Availability Domain อื่นในภูมิภาคเดิม แนะนำดิสก์บูต 50 GB ภายในโควตาฟรี และตรวจว่าหน้าสร้าง VM แสดง **Always Free eligible** ก่อนยืนยัน

## 2) สร้าง VM บน Oracle

1. Oracle Console → Compute → Instances → Create Instance
2. เลือกอิมเมจ **Canonical Ubuntu 24.04** ที่รองรับ Ampere A1 (aarch64/ARM64)
3. Shape: **VM.Standard.A1.Flex (Always Free eligible)** → 1 OCPU, 6 GB RAM (หรือไม่เกินโควตาที่เหลือ)
4. ใส่ SSH public key ของคุณ ในส่วน Networking ให้ VM ต่อออกอินเทอร์เน็ตได้
5. เปิดอินบาวด์เฉพาะ **SSH TCP/22 จาก IP ของคุณ**; ไม่ต้องเปิด TCP/8000, TCP/80, TCP/443 สำหรับแดชบอร์ด เพราะ Cloudflare Tunnel ใช้การเชื่อมต่อขาออก
6. จด Public IP ของ VM แล้ว SSH จากเครื่องตัวเอง (ชื่อผู้ใช้ของอิมเมจ Ubuntu ปกติคือ `ubuntu`)

```bash
ssh -i /path/to/your/private-key ubuntu@<ORACLE_PUBLIC_IP>
```

อย่าส่ง SSH private key หรือ Discord Bot Token มาในแชต

## 3) ติดตั้งโปรเจกต์บน VM

ส่งไฟล์ ZIP ชื่อ `voice-hub-oracle.zip` ที่ได้จาก ChatGPT ไปที่ VM จากคอมพิวเตอร์ตัวเอง:

```bash
scp -i /path/to/your/private-key voice-hub-oracle.zip ubuntu@<ORACLE_PUBLIC_IP>:~/
```

SSH เข้า VM แล้วสั่ง:

```bash
sudo apt-get update
sudo apt-get install -y unzip
unzip voice-hub-oracle.zip
cd discord-voice-hub
sudo bash deploy/oracle-install.sh
```

สคริปต์ตรวจว่าเป็น ARM64, ติดตั้ง Python/FFmpeg/SQLite, ดาวน์โหลด **Deno ARM64 จาก release ทางการพร้อมตรวจ SHA-256**, ติดตั้งไลบรารี Python, สร้างผู้ใช้ระบบ `voicehub` และเตรียม `voicehub.service` สำหรับรันอัตโนมัติ **ยังไม่เปิดบอตจนกว่าจะกรอก .env**

ที่อยู่หลังติดตั้ง:

| ที่อยู่ | ใช้เก็บอะไร |
| --- | --- |
| `/opt/voice-hub/` | โปรแกรม, Python virtualenv, การตั้งค่า |
| `/opt/voice-hub/.env` | Token และข้อมูลลับ (`root:voicehub`, สิทธิ์ 640) |
| `/var/lib/voice-hub/voice_hub.sqlite3` | เพลย์ลิสต์, ตั้งค่าเสียง, OAuth session |
| `/etc/systemd/system/voicehub.service` | บริการที่เริ่มใหม่เมื่อเครื่องบูต |

## 4) สร้าง Discord Application

1. Discord Developer Portal → **New Application** → ตั้งชื่อ Voice Hub
2. **Bot** → คัดลอก Token เก็บเป็นความลับ และเปิด **Message Content Intent** (จำเป็นสำหรับอ่านข้อความในห้อง)
3. **OAuth2 → General** → คัดลอก **Client ID** กับ **Client Secret**
4. **OAuth2 → Redirects** เพิ่ม URL: `https://bot.example.com/auth/callback` โดยเปลี่ยนเป็นโดเมนจริงของคุณ
5. **OAuth2 → URL Generator** เลือก `bot` และ `applications.commands`; ให้สิทธิ์อย่างน้อย View Channels, Send Messages, Connect, Speak แล้วเปิด URL เพื่อเชิญบอตเข้าสู่เซิร์ฟเวอร์

เปิดไฟล์ตั้งค่า:

```bash
sudoedit /opt/voice-hub/.env
```

ใส่ `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` และแก้ `DISCORD_REDIRECT_URI` ให้ตรงกับข้อ 4 **ทุกตัวอักษร** คง `COOKIE_SECURE=true` และ `DATABASE_PATH=/var/lib/voice-hub/voice_hub.sqlite3` ไว้ จากนั้นเริ่มบริการ:

```bash
sudo systemctl start voicehub
sudo systemctl status voicehub --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

ตัวอย่างผล: `{"ok":true,"discord_connected":true}` หากเป็น `false` แปลว่าเว็บรันอยู่แต่ Bot ยังไม่พร้อม ตรวจ Token, Message Content Intent และ log ด้านล่าง

## 5) เชื่อม Cloudflare Tunnel แบบฟรี

1. Cloudflare Dashboard → **Zero Trust / Cloudflare One → Networks → Connectors → Cloudflare Tunnels** → Create Tunnel (เมนูอาจเปลี่ยนชื่อ)
2. เลือก **Cloudflared** แบบ remotely-managed ตั้งชื่อเช่น `voice-hub-oracle`
3. ในหน้าติดตั้ง Connector เลือก **Debian/Ubuntu + ARM64** แล้วใช้คำสั่งติดตั้งจาก Cloudflare บน VM หากไม่มีคำสั่งติดตั้งแพ็กเกจ ให้ใช้:

   ```bash
   curl -fL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o cloudflared-linux-arm64.deb
   sudo apt-get install -y ./cloudflared-linux-arm64.deb
   ```

4. ในหน้าจอ Cloudflare จะมีคำสั่งหน้าตาคล้าย `sudo cloudflared service install <TUNNEL_TOKEN>` ให้คัดลอกจากหน้า Console แล้วรันบน VM โดยตรง **อย่านำ Tunnel Token มาใส่ใน ZIP หรือแชต**
5. เพิ่ม **Published application / Public Hostname**:
   - Subdomain: `bot`
   - Domain: เลือกโดเมนของคุณ เช่น `example.com`
   - Service Type: `HTTP`
   - Service URL: `localhost:8000` หรือ `http://localhost:8000` ตามรูปแบบช่องที่หน้า UI ขอ
6. ตรวจสถานะ Tunnel เป็น Healthy และเปิด `https://bot.example.com`

Cloudflare เป็นตัวรับ HTTPS สำหรับหน้าเว็บเท่านั้น ส่วน Discord Gateway และเสียง UDP เชื่อมออกจาก Oracle VM **โดยตรง** ไม่วิ่งผ่าน Cloudflare Tunnel

ถ้ายังไม่มีโดเมน สามารถใช้ [Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) เพื่อทดลองชั่วคราวได้ แต่ URL จะเปลี่ยนเมื่อเริ่ม Tunnel ใหม่ จึงไม่เหมาะกับ OAuth Redirect ของเว็บใช้งานจริง

## 6) ทดสอบ Discord Bot และเว็บ

1. เข้าห้อง Voice แล้วพิมพ์ `/join` ในห้องข้อความที่ต้องการอ่านออกเสียง
2. ลองพิมพ์ข้อความในห้องนั้น บอตควรอ่านเป็นเสียงภาษาไทย
3. เปิด `https://bot.example.com` → **Login with Discord** → เลือกเสียงหญิง `Premwadee` หรือเสียงชาย `Niwat` และปรับความเร็ว/ระดับเสียง
4. `/play ชื่อเพลง` หรือ `/play https://www.youtube.com/watch?v=...` → `/queue` → `/remove 1`, `/next`, `/skip`, `/playnow`
5. สร้างเพลย์ลิสต์ของตัวเองบนเว็บ เพิ่มเพลง แล้วสั่งเล่นเมื่อคุณอยู่ห้อง Voice เดียวกับบอต

**ข้อจำกัด YouTube:** โค้ดตัวอย่างใช้ yt-dlp + FFmpeg สำหรับทดสอบเทคนิคการเล่นเสียง การแยกเสียงหรือเล่นเสียงพื้นหลังอาจขัดกับข้อกำหนด YouTube และ YouTube เปลี่ยนระบบบ่อยจนทำให้เล่นไม่สำเร็จได้ ควรใช้แหล่งเสียงที่มีสิทธิ์ใช้งานตามเงื่อนไขจริงเมื่อเปิดให้บริการ

## 7) ดู log, บำรุงรักษา และสำรองข้อมูล

```bash
# บอตและหน้าเว็บ
sudo journalctl -u voicehub -n 100 --no-pager
sudo journalctl -u voicehub -f

# Cloudflare Tunnel
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager

# เริ่มใหม่หลังแก้ .env (ใช้กับโค้ดชุดเดียวกันเพียงโปรเซสเดียว)
sudo systemctl restart voicehub
```

**สำรองฐานข้อมูลเป็นระยะ** (มีข้อมูล OAuth session ที่ละเอียดอ่อน อย่าเผยแพร่ไฟล์นี้):

```bash
sudo install -d -m 0700 /root/voice-hub-backups
sudo sqlite3 /var/lib/voice-hub/voice_hub.sqlite3 ".backup '/root/voice-hub-backups/voice-hub-$(date +%Y%m%d).sqlite3'"
```

หากอัปเดตโค้ดในอนาคต ให้สำรอง DB ก่อน หยุดบริการ (`sudo systemctl stop voicehub`), แตก ZIP รุ่นใหม่และรัน `sudo bash deploy/oracle-install.sh` อีกครั้ง ไฟล์ `.env` และข้อมูลใน `/var/lib/voice-hub` จะถูกเก็บไว้ แล้วเริ่ม `sudo systemctl start voicehub`

ข้อควรจำ: อย่าเปิดพอร์ต 8000 ต่ออินเทอร์เน็ต, อย่าส่ง `.env`, อย่าแชร์ Cloudflare Tunnel Token และอย่ารัน Uvicorn หลาย worker เพราะจะสตาร์ต Bot ซ้ำกัน

## เอกสารทางการ

- [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Cloudflare Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/)
- [Cloudflare Tunnel published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Deno install (ARM64)](https://docs.deno.com/runtime/getting_started/installation/)
- [yt-dlp YouTube JavaScript runtime](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
