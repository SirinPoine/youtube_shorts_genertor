# Reels / Shorts Üretici 🎬

Dikey (9:16) kısa video üretici — **seslendirme + kelime vurgulu altyazı + sinematik müzik + Ken Burns hareketli sahneler**.
Tek komutla 1080x1920, 30 fps, H.264 + AAC bir `reel.mp4` üretir; doğrudan Instagram Reels / YouTube Shorts / TikTok'a yüklenebilir.

## Sonuç

- **Çıktı:** [`videos/reel.mp4`](videos/reel.mp4) — 1080x1920, ~38 sn, ~20 MB
- **Konu:** Dopamin döngüsü (telefon bağımlılığı) — kanca, açıklama, çözüm ve "kaydet" çağrısı
- **Ses:** Türkçe erkek seslendirme, altına ducking uygulanmış müzik yatağı
- **Görsel:** 4 adet sinematik arka plan, yavaş zoom/pan hareketi, sahneler arası yumuşak geçiş

## Kurulum

```bash
pip install -r requirements.txt
```

`imageio-ffmpeg` ffmpeg ikilisini kendisi indirir; sistemde ayrıca ffmpeg kurmanız gerekmez.

## Kullanım

```bash
python3 scripts/build_reel.py
```

Girdiler:

| Yol | Açıklama |
| --- | --- |
| `build/bg*.png` | Arka plan görselleri (dikey, 9:16) |
| `build/vo/*.mp3` | Seslendirme parçaları (sırayla numaralanmış) |

Çıktı: `videos/reel.mp4`

## Videoyu değiştirmek

Metin ve sahne planı `scripts/build_reel.py` içindeki `LINES` ve `SCENES` listelerindedir:

```python
LINES = [
    ("Seslendirmede okunacak metin.",
     "Altyazı kartı 1.|Altyazı kartı 2."),   # *yıldızlı* kelimeler sarı vurgulanır
]
SCENES = [("bg1_hook.png", 1.00, 1.16, (-1, 0))]  # görsel, zoom başlangıç, bitiş, pan yönü
```

- **Yeni bir video üretmek için:** `build/vo/` içindeki ses dosyalarını ve gerekiyorsa `build/bg*.png` görsellerini değiştirip `LINES` metnini güncelleyin, sonra scripti tekrar çalıştırın.
- Süreler otomatik: altyazı kartlarının zamanlaması seslendirme uzunluğuna göre hesaplanır.

## Teknik notlar

- **Müzik** harici dosya olmadan numpy ile sentezlenir (pad akorları, alt bas, kalp atışı, riser/whoosh) ve seslendirme sırasında otomatik kısılır (ducking).
- **Altyazılar** PIL ile önceden bitmap olarak çizilir; pop-in ölçek animasyonuyla eklenir, Türkçe karakter desteği vardır.
- **Video** kare kare PIL'de işlenip ffmpeg'e borulanır; `libx264 -crf 18`, `+faststart`.
- Vignette, üst/alt sinematik karartma, üstte ilerleme çubuğu ve finalde "KAYDET" butonu otomatik eklenir.

## Gereksinimler

`numpy`, `pillow`, `imageio-ffmpeg` (bkz. `requirements.txt`)
