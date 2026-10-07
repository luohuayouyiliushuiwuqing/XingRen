"""Raindrop 风格的 Qt 看板：缩略图在上、标题在下的卡片网格。

数据与 CLI 共用 metadata.json。运行：python gui.py
"""

import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from records import STORE_PATH, load_records, merge_record, save_records
from metadata_fetcher import get_metadata

PROXY_DEFAULT = "http://127.0.0.1:7892"
CARD_W = 240
CARD_H = 213
THUMB_H = 135
GAP = 16
MARGIN = 24

QSS = """
QMainWindow, QWidget#Root { background: #f3f4f6; }
QLabel#Brand { font-size: 15px; font-weight: 700; color: #111827; }
QLineEdit {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;
    padding: 7px 12px; font-size: 13px; color: #111827;
}
QLineEdit:focus { border: 1px solid #2f80ed; }
QPushButton#Add {
    background: #2f80ed; color: #ffffff; border: none; border-radius: 8px;
    padding: 7px 18px; font-size: 13px; font-weight: 600;
}
QPushButton#Add:hover { background: #2563eb; }
QPushButton#Add:disabled { background: #a5c9f5; }
QFrame#Card {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
}
QFrame#Card:hover { border: 1px solid #2f80ed; }
QLabel#Title { font-size: 14px; font-weight: 600; color: #111827; }
QLabel#TitlePending { font-size: 14px; font-weight: 600; color: #9ca3af; }
QLabel#Domain { font-size: 12px; color: #6b7280; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


class FetchSignals(QObject):
    finished = pyqtSignal(dict)


class FetchTask(QRunnable):
    """在线程中抓取一个 URL 的元数据。"""

    def __init__(self, url: str, proxy: str | None):
        super().__init__()
        self.signals = FetchSignals()
        self.url = url
        self.proxy = proxy

    def run(self) -> None:
        try:
            meta = get_metadata(self.url, proxy=self.proxy)
        except Exception as exc:  # 抓取层已降级，这里兜住意外错误
            meta = {"url": self.url, "title": "", "thumbnail": "", "favicon": "", "success": False}
            meta["error"] = str(exc)
        self.signals.finished.emit(meta)


class ImageLoader(QObject):
    """异步下载图片，按 URL 缓存，完成后发原始字节（由主线程转 QPixmap）。"""

    loaded = pyqtSignal(str, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._qnam = QNetworkAccessManager(self)
        self._cache: dict[str, bytes] = {}
        self._pending: set[str] = set()

    def get(self, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            return
        if url in self._cache:
            self.loaded.emit(url, self._cache[url])
            return
        if url in self._pending:
            return
        self._pending.add(url)
        reply = self._qnam.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda: self._on_done(reply, url))

    def _on_done(self, reply: QNetworkReply, url: str) -> None:
        self._pending.discard(url)
        reply.deleteLater()
        data = bytes(reply.readAll())
        if reply.error() == QNetworkReply.NoError and data:
            self._cache[url] = data
            self.loaded.emit(url, data)


class ThumbnailLabel(QLabel):
    """带圆角的缩略图：有图用图，否则显示 favicon，再退化为首字母。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(THUMB_H)
        self._pix: QPixmap | None = None
        self._favicon: QPixmap | None = None
        self._letter = ""

    def set_thumbnail(self, data: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            self._pix = pix
            self.update()

    def set_favicon(self, data: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            self._favicon = pix
            self.update()

    def reset(self, letter: str) -> None:
        self._pix = None
        self._favicon = None
        self._letter = letter
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 6, 6)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#e5e7eb"))
        rect = self.rect()
        if self._pix and not self._pix.isNull():
            scaled = self._pix.scaled(
                rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = rect.x() + (rect.width() - scaled.width()) // 2
            y = rect.y() + (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif self._favicon and not self._favicon.isNull():
            scaled = self._favicon.scaled(
                32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = rect.x() + (rect.width() - scaled.width()) // 2
            y = rect.y() + (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QColor("#9ca3af"))
            font = QFont(self.font())
            font.setPointSize(22)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self._letter)
        painter.end()


class Card(QFrame):
    """单张卡片：缩略图在上、标题在下，单击打开，右键菜单操作。"""

    open_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(object)
    refresh_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    copy_requested = pyqtSignal(str)

    def __init__(self, record: dict, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.record = record
        self.loader = loader
        self.match = True  # 搜索过滤标记
        self.setObjectName("Card")
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self.thumb = ThumbnailLabel()
        self.loader.loaded.connect(self._on_image)
        outer.addWidget(self.thumb)

        text = QVBoxLayout()
        text.setContentsMargins(2, 10, 2, 0)
        text.setSpacing(2)
        self.title = QLabel()
        self.title.setFixedHeight(36)
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.domain = QLabel()
        self.domain.setObjectName("Domain")
        self.domain.setFixedHeight(16)
        text.addWidget(self.domain)
        outer.addLayout(text)

        self.set_record(record)

    def set_record(self, record: dict) -> None:
        self.record = record
        url = record["url"]
        host = QUrl(url).host() or url
        pending = not record.get("success") and not record.get("title")
        self.title.setText(record.get("title") or "（标题待补充）")
        self.title.setObjectName("TitlePending" if pending else "Title")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.domain.setText(host)
        self.setToolTip(f"{record.get('title') or ''}\n{url}")

        self.thumb.reset(host[:1].upper() if host else "?")
        if record.get("thumbnail"):
            self.loader.get(record["thumbnail"])
        if record.get("favicon"):
            self.loader.get(record["favicon"])

    def _on_image(self, url: str, data: bytes) -> None:
        if url == self.record.get("thumbnail"):
            self.thumb.set_thumbnail(data)
        elif url == self.record.get("favicon") and self.thumb._pix is None:
            self.thumb.set_favicon(data)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.open_requested.emit(self.record["url"])
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("打开", lambda: self.open_requested.emit(self.record["url"]))
        menu.addAction("复制链接", lambda: self.copy_requested.emit(self.record["url"]))
        menu.addSeparator()
        menu.addAction("重命名…", lambda: self.rename_requested.emit(self))
        menu.addAction("重新抓取", lambda: self.refresh_requested.emit(self.record["url"]))
        menu.addSeparator()
        menu.addAction("删除", lambda: self.delete_requested.emit(self.record["url"]))
        menu.exec_(event.globalPos())


class Board(QWidget):
    """流式网格：按宽度自动换列。"""

    def __init__(self, loader: ImageLoader, on_card_added=None, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.on_card_added = on_card_added
        self.cards: list[Card] = []
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.grid.setHorizontalSpacing(GAP)
        self.grid.setVerticalSpacing(GAP)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

    def add_card(self, card: Card, front: bool = False) -> None:
        if front:
            self.cards.insert(0, card)
        else:
            self.cards.append(card)
        if self.on_card_added:
            self.on_card_added(card)
        self.reflow()

    def remove_card(self, url: str) -> None:
        for card in list(self.cards):
            if card.record["url"] == url:
                self.cards.remove(card)
                card.setParent(None)
                card.deleteLater()
        self.reflow()

    def find_card(self, url: str) -> Card | None:
        for card in self.cards:
            if card.record["url"] == url:
                return card
        return None

    def set_filter(self, text: str) -> None:
        text = text.strip().lower()
        for card in self.cards:
            record = card.record
            haystack = f"{record.get('title', '')} {record['url']}".lower()
            card.match = not text or text in haystack
        self.reflow()

    def reflow(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        visible = [card for card in self.cards if card.match]
        cols = max(1, (self.width() - 2 * MARGIN + GAP) // (CARD_W + GAP))
        for i, card in enumerate(visible):
            self.grid.addWidget(card, i // cols, i % cols)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.reflow()


class MainWindow(QWidget):
    def __init__(self, store_path: Path = STORE_PATH):
        super().__init__()
        self.store_path = store_path
        self.records = load_records(store_path)
        self.tasks: set[FetchTask] = set()
        self.setObjectName("Root")
        self.setWindowTitle("元数据看板")
        self.resize(1120, 740)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        self.loader = ImageLoader(self)
        self.board = Board(self.loader, on_card_added=self._connect_card)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.board)
        root.addWidget(scroll, 1)

        self.status = QLabel()
        self.status.setObjectName("Domain")
        self.status.setContentsMargins(MARGIN, 0, MARGIN, 10)
        root.addWidget(self.status)

        self.search_input.textChanged.connect(self.board.set_filter)
        for record in self.records:
            self.board.add_card(Card(record, self.loader), front=False)
        self.board.reflow()
        self._update_status()

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("QFrame { background: #ffffff; border-bottom: 1px solid #e5e7eb; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(MARGIN, 10, MARGIN, 10)
        row.setSpacing(10)

        brand = QLabel("元数据看板")
        brand.setObjectName("Brand")
        row.addWidget(brand)

        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("粘贴 URL 后回车添加…")
        self.add_input.returnPressed.connect(self.add_url)
        row.addWidget(self.add_input, 1)

        self.add_btn = QPushButton("添加")
        self.add_btn.setObjectName("Add")
        self.add_btn.clicked.connect(self.add_url)
        row.addWidget(self.add_btn)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索标题或链接…")
        self.search_input.setFixedWidth(200)
        row.addWidget(self.search_input)

        self.proxy_input = QLineEdit(PROXY_DEFAULT)
        self.proxy_input.setFixedWidth(170)
        self.proxy_input.setToolTip("抓取代理（留空则直连）")
        row.addWidget(self.proxy_input)
        return bar

    def _connect_card(self, card: Card) -> None:
        card.open_requested.connect(self.open_url)
        card.copy_requested.connect(self.copy_url)
        card.rename_requested.connect(self.rename_card)
        card.refresh_requested.connect(self.start_fetch)
        card.delete_requested.connect(self.delete_record)

    # ---------- 动作 ----------

    def open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def copy_url(self, url: str) -> None:
        QApplication.clipboard().setText(url)
        self.status.setText(f"已复制链接 {url}")

    def add_url(self) -> None:
        text = self.add_input.text().strip()
        if not text:
            return
        if not text.startswith(("http://", "https://")):
            text = "https://" + text
        self.add_input.clear()
        self.start_fetch(text)

    def start_fetch(self, url: str) -> None:
        proxy = self.proxy_input.text().strip() or None
        task = FetchTask(url, proxy)
        task.signals.finished.connect(self.on_fetched)
        task.signals.finished.connect(lambda _m, t=task: self.tasks.discard(t))
        self.tasks.add(task)
        self.add_btn.setEnabled(False)
        self.status.setText(f"正在抓取 {url} …")
        QThreadPool.globalInstance().start(task)

    def on_fetched(self, meta: dict) -> None:
        self.add_btn.setEnabled(True)
        url = meta["url"]
        self.records = merge_record(self.records, meta)
        save_records(self.store_path, self.records)
        merged = next(r for r in self.records if r["url"] == url)
        card = self.board.find_card(url)
        if card:
            card.set_record(merged)
        else:
            self.board.add_card(Card(merged, self.loader), front=True)
        if meta.get("success"):
            self.status.setText(f"已抓取：{merged.get('title') or url}")
        else:
            self.status.setText(f"抓取失败，已仅保存 URL：{url}")

    def rename_card(self, card: Card) -> None:
        text, ok = QInputDialog.getText(
            self, "重命名", "标题:", text=card.record.get("title", "")
        )
        if not ok or not text.strip():
            return
        card.record["title"] = text.strip()
        for record in self.records:
            if record["url"] == card.record["url"]:
                record["title"] = text.strip()
        save_records(self.store_path, self.records)
        card.set_record(card.record)
        self._update_status()

    def delete_record(self, url: str) -> None:
        card = self.board.find_card(url)
        title = (card.record.get("title") or url) if card else url
        answer = QMessageBox.question(
            self, "删除", f"确定删除「{title}」？", QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return
        self.records = [r for r in self.records if r["url"] != url]
        save_records(self.store_path, self.records)
        self.board.remove_card(url)
        self._update_status()

    def _update_status(self) -> None:
        self.status.setText(f"共 {len(self.records)} 条")


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
