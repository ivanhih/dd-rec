from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, ...
from PySide6.QtCore import QTimer

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("B站高级录播机")
        self.resize(1280, 720)
        self.cards = {}          # room_id -> RoomCard

        central = QWidget()
        main_layout = QHBoxLayout(central)

        # 侧边栏
        sidebar = self.build_sidebar()
        main_layout.addWidget(sidebar)

        # 内容区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_container)  # 或 QGridLayout + wrap
        self.cards_layout.setSpacing(15)
        self.scroll.setWidget(self.cards_container)

        main_layout.addWidget(self.scroll)

        self.setCentralWidget(central)

        # 定时刷新 UI
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_all_cards)
        self.refresh_timer.start(2000)

    def add_card(self, room_info):
        card = RoomCard(room_info)
        card.toggle_signal.connect(self.on_toggle)
        # 连接其他 signal...
        self.cards[room_info["room_id"]] = card
        self.cards_layout.addWidget(card)   # 建议改成 QGridLayout 实现自动换行