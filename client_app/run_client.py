import sys
from PyQt5.QtWidgets import QApplication
from client.app import MainWindow

def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec_()

if __name__ == "__main__":
    raise SystemExit(main())
