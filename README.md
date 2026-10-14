# Skinflow Saatlik Çekiliş Alarmı

Skinflow'un saatlik çekilişini izler. Çekilişin bitmesine son 5 dakika kaldığında toplam bilet sayısı ödül değerine göre belirlenen limitin altındaysa Telegram mesajı gönderir.

Varsayılan kurallar:

- Ödül değeri **$0.35 ve altındaysa:** en fazla **800 bilet**
- Ödül değeri **$0.35 üzerindeyse:** en fazla **1000 bilet**
- Aynı çekiliş için yalnızca bir alarm gönderilir.

## Kurulum

Python 3.10 veya üzeri yeterlidir; harici paket gerekmez.

1. Telegram'da `@BotFather` üzerinden bot oluşturup token alın.
2. Botunuza bir mesaj gönderin.
3. Tarayıcıdan `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` adresini açın ve mesajınızdaki `chat.id` değerini bulun.
4. `.env.example` dosyasını `.env` adıyla kopyalayın ve iki Telegram değerini doldurun.

```powershell
Copy-Item .env.example .env
notepad .env
```

## Çalıştırma

Sürekli izleme:

```powershell
python skinflow_alarm.py
```

API bağlantısını Telegram mesajı göndermeden bir kez deneme:

```powershell
python skinflow_alarm.py --once --dry-run
```

Gerçek Telegram ayarlarıyla bir kez kontrol:

```powershell
python skinflow_alarm.py --once
```

Bot çalışır durumda kalmalıdır. Windows açılışında otomatik çalışması için Görev Zamanlayıcı'da `python.exe` programını, `skinflow_alarm.py` argümanını ve bu klasörü başlangıç dizini olarak kullanabilirsiniz.

## Ayarlar

Tüm limitler `.env` içinden değiştirilebilir. Skinflow API'sindeki `giveaway_offered` alanı cent, `total_ticket_amount` alanı toplam katılım bileti olarak değerlendirilir. Aktif çekilişin başlangıç ve bitiş farkı en fazla bir saatse saatlik çekiliş kabul edilir.

Testleri çalıştırmak için:

```powershell
python -m unittest -v
```
