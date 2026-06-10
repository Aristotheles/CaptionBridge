import os
import sys

# PyInstaller giris noktasi: src/ klasorunu yola ekleyip uygulamayi baslatir.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from ceviri_app.main import main


if __name__ == "__main__":
    main()
