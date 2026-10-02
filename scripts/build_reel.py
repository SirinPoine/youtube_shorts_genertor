#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reels / YouTube Shorts üretici (dikey 9:16, seslendirme + altyazı + müzik).

Kullanım:
    python3 scripts/build_reel.py

Girdi : build/bg*.png (arka plan görselleri), build/vo/*.mp3 (seslendirme parçaları)
Çıktı : videos/reel.mp4

Özellikler:
  - Ken Burns (zoom + pan) hareketli sahneler, sahneler arası yumuşak geçiş
  - Kelime vurgulu, animasyonlu altyazılar (pop-in)
  - Numpy ile üretilen sinematik müzik (pad + kalp atışı + riser) ve ses altına ducking
  - Üstte ilerleme çubuğu, sonda "KAYDET" çağrısı
"""
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
VO_DIR = os.path.join(BUILD, "vo")
OUT_DIR = os.path.join(ROOT, "videos")
TMP = os.path.join(BUILD, "tmp")

W, H = 1080, 1920
FPS = 30
SR = 48000
GAP = 0.15          # replikler arası boşluk
LEAD = 0.30         # başta müzik için nefes
TAIL = 1.10         # sonda kapanış
CXFADE = 0.30       # sahne geçiş süresi

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

WHITE = (255, 255, 255, 255)
ACCENT = (255, 214, 0, 255)       # sarı vurgu
SHADOW = (0, 0, 0, 190)

# ----------------------------------------------------------------------------
# 1) İÇERİK
# ----------------------------------------------------------------------------
# Her replik: (metin, | ile ayrılmış altyazı kartları)   *yıldız* = vurgulu kelime
LINES = [
    ("Telefonunu eline aldın. Sadece üç saniye bakacaktın.",
     "Telefonunu eline aldın.|Sadece *üç saniye* bakacaktın."),
    ("Peki neden bırakamıyorsun?",
     "Peki *neden* bırakamıyorsun?"),
    ("Çünkü beynin dopamin arıyor. Ama dopamin, mutluluk değil.",
     "Çünkü beynin *dopamin* arıyor.|Ama dopamin, mutluluk değil."),
    ("Dopamin, arayışın kimyasalıdır. Bulmanın değil.",
     "*Dopamin*, arayışın kimyasalıdır.|Bulmanın değil."),
    ("Her kaydırma, beynine küçük bir belki vaat ediyor. Belki bu komik. Belki bir şey kaçırıyorsun.",
     "Her kaydırma beynine bir *belki* vaat ediyor.|Belki bu komik.|Belki bir şey kaçırıyorsun."),
    ("Ve beyin, kaçırmamak için kaydırmaya devam ediyor.",
     "Ve beyin, kaçırmamak için|*kaydırmaya devam ediyor*."),
    ("Bir saat geçti. Elinde kalan ise hiçbir şey.",
     "Bir saat geçti.|Elinde kalan: *hiçbir şey*."),
    ("Ama bu döngü kırılabilir. Sadece on dakika.",
     "Ama bu döngü *kırılabilir*.|Sadece on dakika."),
    ("Telefonu başka bir odaya bırak. İlk üç dakika beynin çığlık atacak.",
     "Telefonu *başka bir odaya* bırak.|İlk üç dakika beynin çığlık atacak."),
    ("Sonra susacak. Ve gerçek hayat geri gelecek. Kaydet, yarın dene.",
     "Sonra susacak.|Ve gerçek hayat geri gelecek.|*Kaydet*, yarın dene."),
]

# Sahne planı: (görsel, zoom başlangıç, zoom bitiş, pan yönü)
SCENES = [
    ("bg1_hook.png",   1.00, 1.16, (-1, 0)),
    ("bg1_hook.png",   1.18, 1.02, (1, 0)),
    ("bg2_brain.png",  1.00, 1.14, (0, 1)),
    ("bg2_brain.png",  1.16, 1.00, (-1, 0)),
    ("bg3_scroll.png", 1.02, 1.18, (1, 1)),
    ("bg3_scroll.png", 1.20, 1.04, (-1, -1)),
    ("bg3_scroll.png", 1.00, 1.14, (0, -1)),
    ("bg2_brain.png",  1.14, 1.00, (1, 0)),
    ("bg4_free.png",   1.00, 1.16, (0, 1)),
    ("bg4_free.png",   1.18, 1.02, (0, -1)),
]

TOP_BANNER = "BEYNİNİ KONTROL EDEN ŞEY"
TOP_BANNER_UNTIL = 6.0
CTA_TEXT = "KAYDET"
CTA_FROM = None  # toplam süreye göre hesaplanır


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def ffmpeg_bin():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ----------------------------------------------------------------------------
# 2) SESLENDİRME ZAMAN ÇİZELGESİ
# ----------------------------------------------------------------------------
def decode_mp3(path, sr=SR):
    """mp3 -> float32 mono numpy"""
    raw = subprocess.run(
        [ffmpeg_bin(), "-v", "quiet", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        check=True, stdout=subprocess.PIPE).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def load_voice():
    files = sorted(f for f in os.listdir(VO_DIR) if f.endswith(".mp3"))
    clips = [decode_mp3(os.path.join(VO_DIR, f)) for f in files]
    durs = [len(c) / SR for c in clips]
    total = LEAD + sum(durs) + GAP * (len(clips) - 1) + TAIL
    voice = np.zeros(int(total * SR) + SR, dtype=np.float64)
    t0 = LEAD
    spans = []
    for c, d in zip(clips, durs):
        i0 = int(t0 * SR)
        voice[i0:i0 + len(c)] += c
        spans.append((t0, d))
        t0 += d + GAP
    return voice, spans, total


# ----------------------------------------------------------------------------
# 3) MÜZİK (numpy ile sentez) — sinematik pad + kalp atışı + riser
# ----------------------------------------------------------------------------
def note(freq, dur, sr=SR, detune=0.4):
    n = int(dur * sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * 2 * freq * t) * 0.35
    x += 0.5 * np.sin(2 * np.pi * (freq + detune) * t)
    x *= 0.34
    atk = int(0.9 * sr)
    rel = int(1.2 * sr)
    env = np.ones(n)
    env[:atk] = np.linspace(0, 1, atk) ** 1.5
    env[-rel:] *= np.linspace(1, 0, rel) ** 1.4
    return x * env


def lowpass(x, cutoff, sr=SR):
    a = math.exp(-2 * math.pi * cutoff / sr)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def fft_lowpass(x, cutoff, sr=SR):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    X *= 1.0 / (1.0 + (f / cutoff) ** 2)
    return np.fft.irfft(X, n=len(x))


def reverb(x, sr=SR, decay=1.1, wet=0.5):
    n_ir = int(1.8 * sr)
    t = np.arange(n_ir) / sr
    rng = np.random.default_rng(7)
    ir = rng.normal(0, 1, n_ir) * np.exp(-t / decay)
    ir[:int(0.01 * sr)] = 0
    ir = fft_lowpass(ir, 2500, sr)
    ir /= np.max(np.abs(ir))
    n = len(x) + n_ir
    nf = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, nf)
    IR = np.fft.rfft(ir, nf)
    y = np.fft.irfft(X * IR, nf)[:len(x)]
    y /= max(np.max(np.abs(y)), 1e-9)
    return (1 - wet) * x + wet * y * np.max(np.abs(x))


CHORDS = {
    "Am": [110.00, 130.81, 164.81],
    "F":  [87.31, 110.00, 130.81],
    "C":  [130.81, 164.81, 196.00],
    "G":  [98.00, 123.47, 146.83],
}
PROG = ["Am", "Am", "F", "C", "G", "Am", "F", "C", "F", "C"]


def make_music(total):
    n = int(total * SR) + SR
    pad = np.zeros(n)
    slot = total / len(PROG)
    for i, name in enumerate(PROG):
        freqs = CHORDS[name]
        # son bölümde daha parlak ve yükselen bir his
        lift = 1.0 + 0.35 * max(0.0, (i - 6) / (len(PROG) - 7))
        seg = np.zeros(int((slot + 2.5) * SR))
        for f in freqs:
            seg[:len(note(f, slot + 2.5))] += note(f, slot + 2.5) * lift
        if i >= 7:  # tiz shimmer
            seg[:len(note(freqs[-1] * 4, slot + 2.5))] += 0.18 * note(freqs[-1] * 4, slot + 2.5)
        a = int(i * slot * SR)
        take = min(len(seg), n - a)
        if take > 0:
            pad[a:a + take] += seg[:take]

    # alt bas (sub) — yumuşak nefes
    t = np.arange(n) / SR
    sub = 0.22 * np.sin(2 * np.pi * 41.2 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / 9.2))

    # kalp atışı: her 2.5 sn'de lub-dub
    beat = np.zeros(n)
    bt = 0.6
    while bt < total:
        for off, amp in ((0.0, 1.0), (0.34, 0.62)):
            i0 = int((bt + off) * SR)
            dur = int(0.34 * SR)
            if i0 + dur >= n:
                break
            tt = np.arange(dur) / SR
            f = 78 * np.exp(-tt * 9) + 42
            env = np.exp(-tt * 11)
            beat[i0:i0 + dur] += amp * 0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * env
        bt += 2.5
    beat = fft_lowpass(beat, 220)
    beat /= max(np.max(np.abs(beat)), 1e-9)

    # riser / whoosh (giriş + son perde öncesi)
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, n)
    env = np.zeros(n)
    a = int(0.0 * SR)
    b = int(3.0 * SR)
    env[a:b] = np.linspace(0, 1, b - a) ** 2
    c = int(30.5 * SR)
    d = int(33.0 * SR)
    env[c:d] = np.linspace(0, 1, d - c) ** 1.6 * 0.8
    e = int(33.0 * SR)
    g = int(35.0 * SR)
    env[e:g] += np.linspace(0.8, 0, g - e)
    noise = fft_lowpass(noise, 1800) * env * 0.5
    noise /= max(np.max(np.abs(noise)), 1e-9)

    music = pad / max(np.max(np.abs(pad)), 1e-9)
    music = reverb(music, wet=0.42) * 0.55 + sub * 0.20 + beat * 0.22 + noise * 0.16
    music /= max(np.max(np.abs(music)), 1e-9)
    return music[:n] * 0.85


# ----------------------------------------------------------------------------
# 4) MİKSAJ (seslendirme + ducking uygulanmış müzik)
# ----------------------------------------------------------------------------
def smooth_env(x, atk, rel, sr=SR):
    n = len(x)
    a_a = math.exp(-1.0 / (atk * sr))
    a_r = math.exp(-1.0 / (rel * sr))
    y = np.empty(n)
    acc = 0.0
    for i in range(n):
        c = a_a if x[i] > acc else a_r
        acc = c * acc + (1 - c) * x[i]
        y[i] = acc
    return y


def mix(voice, music):
    n = max(len(voice), len(music))
    v = np.pad(voice, (0, n - len(voice)))
    m = np.pad(music, (0, n - len(music)))
    env = smooth_env(np.abs(v), 0.012, 0.35)
    env = np.clip(env / (np.percentile(env, 99.5) + 1e-9), 0, 1)
    duck = 1.0 - 0.72 * np.clip(env * 1.6, 0, 1)
    duck = smooth_env(duck, 0.25, 0.10)  # ducking'in kendisini de yumuşat
    m = m * duck
    v = np.tanh(v * 1.25) * 0.95      # yumuşak kompresör
    mixdown = v * 0.92 + m * 0.42
    peak = np.max(np.abs(mixdown)) + 1e-9
    mixdown = mixdown / peak * 0.92
    st = np.stack([mixdown, mixdown], axis=1)
    return st


def write_wav(path, stereo):
    pcm = np.clip(stereo, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ----------------------------------------------------------------------------
# 5) GÖRSEL: altyazı kartları + sahne motoru
# ----------------------------------------------------------------------------
def parse(text):
    """*vurgu* -> (kelime, vurgulu?)"""
    out = []
    for chunk in text.split():
        if chunk.startswith("*") and chunk.endswith("*") and len(chunk) > 2:
            out.append((chunk[1:-1], True))
        elif chunk.startswith("*"):
            out.append((chunk[1:], True))
        elif chunk.endswith("*"):
            out.append((chunk[:-1], True))
        else:
            out.append((chunk, False))
    return out


def load_font(size):
    return ImageFont.truetype(FONT_BOLD, size)


def render_caption(text, size=84, max_w=940):
    """Altyazı kartını RGBA görsel olarak çiz (vurgulu kelimeler sarı)."""
    font = load_font(size)
    words = parse(text)
    lines, cur, cur_w = [], [], 0
    space = font.getlength(" ")
    for w, hi in words:
        wl = font.getlength(w)
        add = wl if not cur else wl + space
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w = [(w, hi, wl)], wl
        else:
            cur.append((w, hi, wl))
            cur_w += add
    if cur:
        lines.append(cur)

    pad = size  # stroke + gölge payı
    line_h = int(size * 1.30)
    w_img = int(max_w + pad * 2)
    h_img = line_h * len(lines) + pad * 2
    img = Image.new("RGBA", (w_img, h_img), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for li, line in enumerate(lines):
        total = sum(x[2] for x in line) + space * (len(line) - 1)
        x = (w_img - total) / 2
        y = pad + li * line_h
        for w, hi, wl in line:
            for dx, dy, col in ((6, 8, SHADOW), (0, 0, ACCENT if hi else WHITE)):
                d.text((x + dx, y + dy), w, font=font, fill=col,
                       stroke_width=int(size * 0.10),
                       stroke_fill=(0, 0, 0, col[3]) if col is SHADOW else (8, 8, 12, 255))
            x += wl + space
    return img


def caption_cards(spans, total):
    """Her replik için altyazı kartları ve zaman aralıkları."""
    cards = []
    for (t0, dur), (text, cards_txt) in zip(spans, LINES):
        chunks = cards_txt.split("|")
        weights = [max(len(c.replace("*", "")), 4) for c in chunks]
        tot = sum(weights)
        ct = t0
        for c, wgt in zip(chunks, weights):
            cd = dur * wgt / tot
            cards.append({"img": render_caption(c), "t0": ct, "t1": ct + cd})
            ct += cd
    return cards


def make_overlays():
    """Vignette + üst karartma + ilerleme çubuğu zemini (tek seferlik)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # üst ve alt sinematik karartma
    grad = Image.new("RGBA", (1, H), (0, 0, 0, 0))
    px = grad.load()
    for y in range(H):
        a = 0
        if y < 420:
            a = int(120 * (1 - y / 420) ** 1.6)
        if y > H - 520:
            a = max(a, int(135 * ((y - (H - 520)) / 520) ** 1.5))
        px[0, y] = (0, 0, 0, a)
    ov = Image.alpha_composite(ov, grad.resize((W, H)))
    # hafif vinyet
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    a = np.clip((r - 0.62) * 190, 0, 150).astype(np.uint8)
    vig = Image.fromarray(np.dstack([np.zeros((H, W), np.uint8)] * 3 + [a]), "RGBA")
    return Image.alpha_composite(ov, vig)


def draw_progress(frame, t, total):
    p = min(max(t / total, 0), 1)
    d = ImageDraw.Draw(frame)
    d.rectangle([0, 0, W, 9], fill=(255, 255, 255, 34))
    d.rectangle([0, 0, int(W * p), 9], fill=(255, 214, 0, 235))


def draw_banner(frame, t):
    if t > TOP_BANNER_UNTIL:
        return
    alpha = 1.0 if t < TOP_BANNER_UNTIL - 0.8 else max(0.0, (TOP_BANNER_UNTIL - t) / 0.8)
    # girişte aşağıdan kayarak gelir
    rise = int(max(0.0, 1 - t / 0.5) * 40)
    font = load_font(46)
    tw = font.getlength(TOP_BANNER)
    pad = 34
    box_w, box_h = int(tw + pad * 2), 92
    box = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(box)
    d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=box_h // 2,
                        fill=(255, 255, 255, int(28 * alpha)),
                        outline=(255, 214, 0, int(210 * alpha)), width=4)
    d.text((box_w / 2, box_h / 2), TOP_BANNER, font=font, anchor="mm",
           fill=(255, 255, 255, int(255 * alpha)))
    frame.alpha_composite(box, (int((W - box_w) / 2), 210 + rise))


def draw_cta(frame, t, total):
    if t < total - 4.2:
        return
    a = min(1.0, (t - (total - 4.2)) / 0.35)
    pulse = 1.0 + 0.045 * math.sin((t - (total - 4.2)) * 7.5)
    font = load_font(64)
    tw = font.getlength(CTA_TEXT)
    pad = 46
    bw = int((tw + pad * 2 + 96) * pulse)
    bh = int(140 * pulse)
    box = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(box)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=bh // 2,
                        fill=(255, 214, 0, int(240 * a)))
    # bookmark ikonu
    ix, iy, iw, ih = 54, bh / 2 - 38, 60, 76
    d.polygon([(ix, iy), (ix + iw, iy), (ix + iw, iy + ih),
               (ix + iw / 2, iy + ih * 0.66), (ix, iy + ih)],
              fill=(16, 16, 20, int(255 * a)))
    d.text((ix + iw + 32, bh / 2), CTA_TEXT, font=font, anchor="lm",
           fill=(16, 16, 20, int(255 * a)))
    frame.alpha_composite(box, (int((W - bw) / 2), 620))


class SceneEngine:
    def __init__(self):
        self.cache = {}
        for name, *_ in SCENES:
            if name in self.cache:
                continue
            im = Image.open(os.path.join(BUILD, name)).convert("RGB")
            # Ken Burns için pay bırakarak büyüt
            self.cache[name] = im.resize((int(W * 1.25), int(H * 1.25)), Image.LANCZOS)

    def render(self, idx, local_t, dur):
        name, z0, z1, (px, py) = SCENES[idx]
        src = self.cache[name]
        p = min(max(local_t / max(dur, 1e-6), 0), 1.35)
        z = z0 + (z1 - z0) * p
        sw, sh = src.size
        cw, ch = W / z, H / z
        cx = sw / 2 + px * (sw - cw) * 0.30 * (p - 0.5) * 2
        cy = sh / 2 + py * (sh - ch) * 0.30 * (p - 0.5) * 2
        cx = min(max(cx, cw / 2), sw - cw / 2)
        cy = min(max(cy, ch / 2), sh - ch / 2)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return src.resize((W, H), Image.BICUBIC, box=box).convert("RGBA")


def render_video(spans, total, overlay, cards, out_path):
    eng = SceneEngine()
    cta_from = total - 4.2
    scene_t = []
    t = 0.0
    for i, (st, d) in enumerate(spans):
        scene_t.append((t, d))
        t += d + GAP
    n_frames = int(total * FPS)
    ff = ffmpeg_bin()
    cmd = [
        ff, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", os.path.join(TMP, "mix.wav"),
        "-filter_complex",
        "[0:v]format=yuv420p,eq=contrast=1.05:saturation=1.10:brightness=0.005[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-shortest",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    card_i = 0
    for fi in range(n_frames):
        t = fi / FPS
        # aktif sahne (+ geçiş yumuşatma)
        idx = 0
        for i, (st, d) in enumerate(scene_t):
            if t >= st:
                idx = i
        st, d = scene_t[idx]
        frame = eng.render(idx, t - st, d)
        if idx > 0 and t - st < CXFADE:
            pst, pd = scene_t[idx - 1]
            prev = eng.render(idx - 1, t - pst, pd)
            a = (t - st) / CXFADE
            frame = Image.blend(prev, frame, a)
        frame = Image.alpha_composite(frame, overlay)
        # altyazı kartları
        while card_i < len(cards) and t > cards[card_i]["t1"]:
            card_i += 1
        if card_i < len(cards) and cards[card_i]["t0"] <= t <= cards[card_i]["t1"]:
            c = cards[card_i]
            life = c["t1"] - c["t0"]
            el = t - c["t0"]
            fade_in = min(1.0, el / 0.16)
            fade_out = min(1.0, max(0.0, (c["t1"] - t) / 0.12))
            alpha = min(fade_in, fade_out)
            img = c["img"]
            scale = 0.90 + 0.10 * min(1.0, el / 0.20)
            rise = int((1 - min(1.0, el / 0.22)) * 26)
            if scale < 0.999:
                nw, nh = int(img.width * scale), int(img.height * scale)
                shown = img.resize((nw, nh), Image.BILINEAR)
                px = int((W - nw) / 2)
                py = int(H * 0.68 - nh / 2) + rise
                base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                base.alpha_composite(shown, (max(px, 0), max(py, 0)))
                shown = base
            else:
                base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                base.alpha_composite(img, (int((W - img.width) / 2),
                                           int(H * 0.68 - img.height / 2) + rise))
                shown = base
            if alpha < 0.999:
                al = shown.getchannel("A").point(lambda v: int(v * alpha))
                shown.putalpha(al)
            frame = Image.alpha_composite(frame, shown)
        draw_progress(frame, t, total)
        draw_banner(frame, t)
        if t >= cta_from:
            draw_cta(frame, t, total)
        proc.stdin.write(frame.convert("RGBA").tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise SystemExit("ffmpeg hatası")


def main():
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    print("• seslendirme okunuyor…")
    voice, spans, total = load_voice()
    print(f"  toplam süre: {total:.2f} sn")
    print("• müzik üretiliyor…")
    music = make_music(total)
    print("• miksaj…")
    write_wav(os.path.join(TMP, "mix.wav"), mix(voice, music))
    print("• altyazılar hazırlanıyor…")
    cards = caption_cards(spans, total)
    overlay = make_overlays()
    print("• video render ediliyor… (bu birkaç dakika sürebilir)")
    out = os.path.join(OUT_DIR, "reel.mp4")
    render_video(spans, total, overlay, cards, out)
    print("✓ hazır:", out)


if __name__ == "__main__":
    sys.exit(main())
