# CaptionBridge 🎙️🌐

**CaptionBridge**, Windows üzerinde çalışan canlı konuşma ayıklama, anlık çeviri ve AI destekli mülakat asistanı uygulamasıdır. Azure Speech SDK ile düşük gecikmeli transkripsiyon, Google Gemini ile akıllı cevap üretimi sağlar.

[English](#english) | [Türkçe](#türkçe)

---

## Türkçe

### Özellikler

#### Canlı Çeviri
- **Canlı Altyazı & Çeviri:** Konuşmaları anlık olarak metne döker ve seçilen dile çevirir.
- **Çoklu Dil Desteği:** Arayüz dili olarak Türkçe, İngilizce ve Almanca seçenekleri.
- **Gelişmiş Ses Yakalama:** Sistem sesi (Stereo Mix), mikrofon veya sanal ses aygıtları üzerinden yakalama.
- **Geçmiş Arşivleme:** Konuşma geçmişini JSON formatında dışa aktarabilir ve tekrar içeri aktarabilirsiniz.

#### AI Mülakat Asistanı (Google Gemini)
- **Anlık Cevap Üretimi:** Algılanan soruyu alıp Gemini'ye gönderir; söylenecek en iyi cevabı Almanca (veya kaynak dilde) ve Türkçe (veya hedef dilde) ayrı panellerde gösterir.
- **Kişisel Profil:** Yapay zekaya kendinizi tanıtabileceğiniz "Profil" alanı — AI cevapları profilinize göre kişiselleştirir.
- **Ayarlanabilir Model:** Gemini key ve model adı (örn. `gemini-2.0-flash`) arayüzden girilebilir, kod değiştirmeye gerek yok.

#### Panel & Arayüz
- **Ayrılabilir Paneller:** Her paneli bağımsız yüzen pencereye taşıyabilirsiniz.
- **Renkli Yüzen Pencereler:** Almanca paneller kırmızı, Türkçe paneller mavi başlık şeridiyle gösterilir.
- **Panel Başına Font Kontrolü:** Her kutuda bağımsız `+` / `-` butonları; yüzen pencerelerde de çalışır.
- **Yumuşak "Her Zaman Üstte":** Başka uygulamaya geçince arkaplanlaşır, geri dönünce öne gelir.
- **Sağ Tık Menüsü:** Tüm metin kutularında Kes / Kopyala / Yapıştır.

### Kurulum (Geliştiriciler İçin)

1. **Repoyu Klonlayın:**
   ```bash
   git clone https://github.com/Aristotheles/CaptionBridge.git
   cd CaptionBridge
   ```

2. **Sanal Ortam & Bağımlılıklar:**
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   pip install google-genai
   ```

3. **Çalıştırma:**
   ```powershell
   python run_ceviri_app.py
   ```

### Azure Speech Ayarları
1. [Azure Portal](https://portal.azure.com/) üzerinden bir **Speech Service** kaynağı oluşturun.
2. **Key** ve **Region** (örn: `westeurope`) bilgilerini uygulama ayarlarına girin.

### Gemini AI Ayarları
1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) adresinden ücretsiz API key alın.
2. Uygulamadaki **Gemini Key** kutusuna yapıştırın.
3. **Model** alanına kullanmak istediğiniz modeli yazın (varsayılan: `gemini-2.0-flash`).

### Ses Ayarları Hakkında Önemli Not
- **Stereo Mix:** Ses kartınız destekliyorsa en stabil yöntemdir.
- **Loopback:** Donanım desteği yoksa yedek yöntem olarak seçilebilir.
- **Virtual Audio Device:** VB-Cable gibi yazılımlar en yüksek başarıyı sağlar.

---

## English

### Features

#### Live Transcription & Translation
- **Live Captioning & Translation:** Real-time speech-to-text with instant translation.
- **Multilingual UI:** Turkish, English, and German interface options.
- **Versatile Audio Capture:** System Audio (Stereo Mix), Microphones, and Virtual Audio Devices.
- **History Management:** Export and import conversation history in JSON format.

#### AI Interview Assistant (Google Gemini)
- **Instant Answer Generation:** Captures the detected question, sends it to Gemini, and displays the best spoken answer in the source language (e.g. German) alongside a translation in the target language (e.g. Turkish).
- **Personal Profile:** Teach the AI about yourself — answers are personalized to your background.
- **Configurable Model:** Set your Gemini API key and model name (e.g. `gemini-2.0-flash`) directly in the UI.

#### Panels & UI
- **Detachable Panels:** Float any panel into its own window.
- **Color-coded Floating Windows:** German panels get a red header, Turkish panels get a blue header.
- **Per-panel Font Control:** Independent `+` / `-` buttons on every panel, including floating windows.
- **Soft Always-on-Top:** Steps back when you click another app, returns to front when you come back.
- **Right-click Context Menu:** Cut / Copy / Paste on all text fields.

### Installation

1. **Clone the Repo:**
   ```bash
   git clone https://github.com/Aristotheles/CaptionBridge.git
   cd CaptionBridge
   ```

2. **Environment Setup:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   pip install google-genai
   ```

3. **Run:**
   ```powershell
   python run_ceviri_app.py
   ```

### Azure Speech Setup
1. Create a **Speech Service** resource in the [Azure Portal](https://portal.azure.com/).
2. Enter your **Key** and **Region** (e.g. `westeurope`) in the app settings.

### Gemini AI Setup
1. Get a free API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Paste it into the **Gemini Key** field in the app.
3. Set the model name in the **Model** field (default: `gemini-2.0-flash`).

### Audio Capture Notes
- **Stereo Mix:** Best stability if available in your sound settings.
- **Loopback:** Fallback for machines without Stereo Mix support.
- **Virtual Audio Device:** Recommended for professional routing (e.g., VB-Cable).

### License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
