# CeviriApp Teknik Analiz

Bu klasorde kaynak kod deposu yok. Analiz, `CeviriApp.exe` icindeki paketlenmis Python bytecode'u uzerinden yapildi.

## Tespit Edilen Yapi

- Uygulama `PyInstaller` ile tek dosya Windows exe olarak paketlenmis.
- Giris modulu `Çeviri.py`.
- Arayuz `tkinter`.
- Ses yakalama `sounddevice`.
- Ceviri/konusma tanima `azure.cognitiveservices.speech`.
- Ornekleme donusumu icin `numpy` ve `scipy.signal.resample` kullaniliyor.

## Kritik Sorunlar

1. Azure anahtari binary icine gomulu.
   - `SPEECH_KEY='...'`
   - `SPEECH_REGION='westeurope'`
   - Bu anahtar GitHub oncesi derhal rotate edilmeli ve repoya kesinlikle konmamali.

2. Kaynak kod yok.
   - Bu haliyle GitHub'a yalnizca binary koyabilirsiniz.
   - Bakim, issue takibi ve katkici kabul etmek icin `.py` kaynaklar gerekli.

3. Kod tek modulde toplanmis.
   - UI, audio capture, Azure istemcisi, loglama ve state ayni dosyada.
   - Test edilebilirlik ve degisiklik maliyeti dusuk.

4. Cihaz secimi sabit.
   - `STEREO_MIX_DEVICE_ID = 27`
   - Farkli makinelerde kirilacak.

5. Paket boyutu gereksiz buyuk.
   - Exe yaklasik 58.7 MB.
   - `numpy/scipy` ve iliskili ikili dosyalar boyutun buyuk kismini olusturuyor.

6. Eski/yarim kalmis kod izi var.
   - `LoopbackAudioStream` sinifi `sc` degiskenine bagli ama modulde `sc` import edilmemis.
   - Bu sinif aktif akista kullanilmiyor.

## GitHub Oncesi Onerilen Is Plani

1. Kaynak kodu geri kazanin veya yeniden olusturun.
2. Azure anahtarini rotate edin, `.env` ve `.env.example` yapisina gecin.
3. Kodu parcalayin:
   - `app.py`
   - `ui.py`
   - `audio.py`
   - `translator.py`
   - `config.py`
4. Sabit cihaz ID'sini kaldirin, cihazlari runtime'da listeleyin.
5. `scipy` bagimliligini azaltin veya daha hafif bir cozum kullanin.
6. `README.md`, `LICENSE`, `.gitignore`, `requirements.txt` veya `pyproject.toml` ekleyin.
7. En azindan config, cihaz secimi ve event isleme icin test ekleyin.

## Not

Analiz icin cikarilan giris bytecode dosyasi:

- `C:\Çeviri\_analysis\Ceviri.pyc`
