# CaptionBridge 🎙️🌐

**CaptionBridge**, Windows üzerinde çalışan canlı konuşma ayıklama, altyazı oluşturma ve anlık çeviri uygulamasıdır. Azure Speech SDK kullanarak düşük gecikmeli ve yüksek doğruluklu sonuçlar sağlar.

[English](#english) | [Türkçe](#türkçe)

---

## Türkçe

### Özellikler
- **Canlı Altyazı & Çeviri:** Konuşmaları anlık olarak metne döker ve seçilen dile çevirir.
- **Çoklu Dil Desteği:** Arayüz dili olarak Türkçe, İngilizce ve Almanca seçenekleri.
- **Esnek Panel Yönetimi:** Anlık ve final metin panellerini ayırabilir, ayrı pencerelere taşıyabilirsiniz.
- **"Her Zaman Üstte" Modu:** Uygulamayı diğer pencerelerin üzerinde tutarak canlı takibi kolaylaştırır.
- **Gelişmiş Ses Yakalama:** Sistem sesi (Stereo Mix), mikrofon veya sanal ses aygıtları üzerinden yakalama.
- **Azure Entegrasyonu:** Doğrudan Azure Speech anahtarınızla çalışır.
- **Geçmiş Arşivleme:** Konuşma geçmişini JSON formatında dışa aktarabilir ve tekrar içeri aktarabilirsiniz.

### Kurulum (Geliştiriciler İçin)
Proje Python tabanlıdır ve Azure Speech SDK kullanır.

1. **Repoyu Klonlayın:**
   ```bash
   git clone https://github.com/kullanici/CaptionBridge.git
   cd CaptionBridge
   ```

2. **Sanal Ortam Kurulumu:**
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   ```

3. **Çalıştırma:**
   ```powershell
   caption-bridge
   ```

### Azure Ayarları
Uygulamayı kullanmak için kendi Azure Speech Key'inize ihtiyacınız vardır.
1. [Azure Portal](https://portal.azure.com/) üzerinden bir **Speech Service** kaynağı oluşturun.
2. Alacağınız **Key** ve **Region** (örn: `westeurope`) bilgilerini uygulama ayarlarına girin.
3. Uygulama içindeki **"Azure Harcama"** butonu, kullanımınızı takip etmeniz için portalı açar.

### Ses Ayarları Hakkında Önemli Not
Sistem sesini yakalamak evrensel bir standart değildir ve donanımınıza bağlıdır:
- **Stereo Mix:** Ses kartınız destekliyorsa en stabil yöntemdir.
- **Loopback:** Donanım desteği yoksa yedek yöntem olarak seçilebilir.
- **Virtual Audio Device:** VB-Cable gibi yazılımlar en yüksek başarıyı sağlar.

---

## English

### Features
- **Live Captioning & Translation:** Transcribes speech in real-time and translates it into your target language.
- **Multilingual UI:** Support for Turkish, English, and German interfaces.
- **Detachable Panels:** Separate and move transcript panels to stay focused.
- **Always on Top:** Keep the translation window visible over other applications.
- **Versatile Audio Capture:** Supports System Audio (Stereo Mix), Microphones, and Virtual Audio Devices.
- **Azure Powered:** High accuracy powered by Azure Speech SDK (BYO Key).
- **History Management:** Export and import your conversation history in JSON format.

### Installation
1. **Clone the Repo:**
   ```bash
   git clone https://github.com/user/CaptionBridge.git
   cd CaptionBridge
   ```

2. **Environment Setup:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e .
   ```

3. **Run:**
   ```powershell
   caption-bridge
   ```

### Audio Capture Disclaimer
System audio capture depends on your Windows audio drivers and hardware.
- **Stereo Mix:** Best stability if available in your sound settings.
- **Loopback:** Used as a fallback for machines without Stereo Mix support.
- **Virtual Audio Device:** Recommended for tools like Zoom or professional routing (e.g., VB-Cable).

### License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
