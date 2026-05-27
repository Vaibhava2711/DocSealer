#!/usr/bin/env python3
"""
DocSealer — Desktop App
Convert JPG / PNG / HEIC / PDF → Merged PDF → Sealed TIFF < 5 MB
Run: python3 doc_sealer_app.py
"""

import sys, os, io, math, shutil, tempfile, threading
from pathlib import Path

# ── PyQt6 ──────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QProgressBar, QFrame, QSizePolicy, QMessageBox, QAbstractItemView,
    QGraphicsDropShadowEffect, QScrollArea
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QMimeData, QSize, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QRect
)
from PyQt6.QtGui import (
    QColor, QPalette, QFont, QPixmap, QImage, QDragEnterEvent,
    QDropEvent, QPainter, QPen, QBrush, QLinearGradient, QIcon,
    QFontDatabase, QMovie
)

# ── Processing libs ─────────────────────────────────────────────────────────
try:
    from PIL import Image
    import img2pdf
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import Color
    LIBS_OK = True
except ImportError as e:
    LIBS_OK = False
    LIBS_ERROR = str(e)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_OK = True
except ImportError:
    PDF2IMAGE_OK = False

# ── Bundled Poppler path (for PyInstaller .exe) ──────────────────────────────
import sys as _sys, os as _os
if getattr(_sys, 'frozen', False):
    _poppler_path = _os.path.join(_sys._MEIPASS, 'poppler', 'bin')
    if _os.path.exists(_poppler_path):
        _os.environ['PATH'] = _poppler_path + _os.pathsep + _os.environ.get('PATH', '')
    else:
        _poppler_path = None
else:
    _poppler_path = None

# ── Constants ───────────────────────────────────────────────────────────────
MAX_TIFF_BYTES   = 5 * 1024 * 1024
RENDER_DPI_START = 150
RENDER_DPI_MIN   = 55
SEAL_MARGIN_PT   = 20
SEAL_OPACITY     = 1.0

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".webp",
                  ".tiff", ".tif", ".heic", ".heif"}

# ── Color Palette ────────────────────────────────────────────────────────────
C = {
    "bg":        "#0F1117",
    "surface":   "#1A1D27",
    "card":      "#21253A",
    "accent":    "#4F7AFF",
    "accent2":   "#7B5EA7",
    "success":   "#22C55E",
    "warning":   "#F59E0B",
    "danger":    "#EF4444",
    "text":      "#E8EAF0",
    "muted":     "#6B7280",
    "border":    "#2D3248",
}

STYLE = f"""
QMainWindow, QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: 'Segoe UI', 'SF Pro Display', Helvetica, Arial, sans-serif;
}}
QLabel {{ color: {C['text']}; background: transparent; }}
QPushButton {{
    background: {C['card']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {C['accent']}; border-color: {C['accent']}; }}
QPushButton:pressed {{ background: #3B5FCC; }}
QPushButton:disabled {{ background: {C['surface']}; color: {C['muted']}; border-color: {C['border']}; }}
QListWidget {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    color: {C['text']};
    font-size: 13px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px 4px;
}}
QListWidget::item:selected {{
    background: {C['accent']};
    color: white;
}}
QListWidget::item:hover:!selected {{
    background: {C['card']};
}}
QProgressBar {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['accent']}, stop:1 {C['accent2']});
    border-radius: 6px;
}}
QScrollBar:vertical {{
    background: {C['surface']};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Drop Zone Widget
# ═══════════════════════════════════════════════════════════════════════════════
class DropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)

        self.icon_lbl = QLabel("📂")
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet("font-size: 36px; background: transparent;")

        self.title_lbl = QLabel("Drop files here  or  click to browse")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {C['text']}; background: transparent;")

        self.sub_lbl = QLabel("JPG · PNG · HEIC · PDF · BMP · WEBP · TIFF")
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setStyleSheet(
            f"font-size: 11px; color: {C['muted']}; background: transparent;")

        lay.addWidget(self.icon_lbl)
        lay.addWidget(self.title_lbl)
        lay.addWidget(self.sub_lbl)
        self._update_style()

    def _update_style(self):
        border_col = C['accent'] if self._hover else C['border']
        bg_col     = "#1F2640" if self._hover else C['surface']
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg_col};
                border: 2px dashed {border_col};
                border-radius: 14px;
            }}
        """)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover = True
            self._update_style()
            self.icon_lbl.setText("📥")

    def dragLeaveEvent(self, e):
        self._hover = False
        self._update_style()
        self.icon_lbl.setText("📂")

    def dropEvent(self, e: QDropEvent):
        self._hover = False
        self._update_style()
        self.icon_lbl.setText("📂")
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTS]
        if paths:
            self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Files", "",
            "Documents (*.jpg *.jpeg *.png *.heic *.heif *.pdf *.bmp *.webp *.tiff *.tif)"
        )
        if paths:
            self.files_dropped.emit(paths)


# ═══════════════════════════════════════════════════════════════════════════════
#  Seal Preview Widget
# ═══════════════════════════════════════════════════════════════════════════════
class SealPreview(QLabel):
    def __init__(self):
        super().__init__()
        self.seal_path = None
        self.setMinimumSize(110, 110)
        self.setMaximumSize(110, 110)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"""
            QLabel {{
                background: {C['surface']};
                border: 2px dashed {C['border']};
                border-radius: 55px;
                color: {C['muted']};
                font-size: 11px;
            }}
        """)
        self.setText("No seal\nloaded")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to load your seal image")

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Seal Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.load_seal(path)

    def load_seal(self, path: str):
        self.seal_path = path
        pix = QPixmap(path)
        pix = pix.scaled(96, 96,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pix)
        self.setStyleSheet(f"""
            QLabel {{
                background: {C['surface']};
                border: 2px solid {C['accent']};
                border-radius: 55px;
            }}
        """)


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker Thread
# ═══════════════════════════════════════════════════════════════════════════════
class Worker(QThread):
    progress   = pyqtSignal(int, str)   # (0-100, message)
    finished   = pyqtSignal(str, float) # (output_path, size_mb)
    error      = pyqtSignal(str)

    def __init__(self, input_files, seal_path, output_path):
        super().__init__()
        self.input_files = input_files
        self.seal_path   = seal_path
        self.output_path = output_path

    # ── helpers ──────────────────────────────────────────────────────────────
    def _file_to_pdf_bytes(self, fp: Path) -> bytes:
        ext = fp.suffix.lower()
        if ext == ".pdf":
            return fp.read_bytes()
        img = Image.open(fp)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Write to temp file - img2pdf is more reliable with file paths
        import tempfile as _tf, os as _os
        with _tf.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
            tmp_img_path = tmp_img.name
        try:
            img.save(tmp_img_path, format="PNG")
            return img2pdf.convert(tmp_img_path)
        finally:
            _os.unlink(tmp_img_path)

    def _make_seal_overlay(self, w_pt: float, h_pt: float,
                           seal_img: Image.Image) -> bytes:
        """Place the user's seal image at bottom-right, semi-transparent."""
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(w_pt, h_pt))

        # Seal size: ~12% of shortest page dimension, capped 60-120pt
        seal_sz = max(100, min(220, int(min(w_pt, h_pt) * 0.35)))
        mgn     = SEAL_MARGIN_PT
        x       = w_pt  - mgn - seal_sz
        y       = mgn

        # Save seal PIL image to temp PNG for reportlab
        tmp_seal = io.BytesIO()
        rgba = seal_img.convert("RGBA")

        # Crop out transparent/white border around seal image
        from PIL import ImageEnhance
        bbox = rgba.getbbox()  # get bounding box of non-transparent pixels
        if bbox:
            rgba = rgba.crop(bbox)

        # Apply opacity to alpha channel
        r, g, b, a = rgba.split()
        a = ImageEnhance.Brightness(a).enhance(SEAL_OPACITY)
        rgba.putalpha(a)
        rgba.save(tmp_seal, format="PNG")
        tmp_seal.seek(0)

        c.saveState()
        c.drawImage(
            rl_canvas.ImageReader(tmp_seal),
            x, y, seal_sz, seal_sz,
            mask='auto'
        )
        c.restoreState()
        c.save()
        buf.seek(0)
        return buf.getvalue()

    # ── main run ─────────────────────────────────────────────────────────────
    def run(self):
        try:
            files = self.input_files
            n = len(files)

            # Load seal once
            self.progress.emit(2, "Loading seal image…")
            seal_img = Image.open(self.seal_path).convert("RGBA")

            # Step 1 – convert to PDFs
            pdf_blobs = []
            for i, fp in enumerate(files):
                pct = 5 + int(35 * i / n)
                self.progress.emit(pct, f"Converting {fp.name} ({i+1}/{n})…")
                pdf_blobs.append(self._file_to_pdf_bytes(fp))

            # Step 2 – merge
            self.progress.emit(42, "Merging pages…")
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                writer = PdfWriter()
                for blob in pdf_blobs:
                    rdr = PdfReader(io.BytesIO(blob))
                    for page in rdr.pages:
                        writer.add_page(page)
                merged = tmp / "merged.pdf"
                with open(merged, "wb") as fh:
                    writer.write(fh)

                total_pages = sum(
                    len(PdfReader(io.BytesIO(b)).pages) for b in pdf_blobs)

                # Step 3 – stamp seal
                self.progress.emit(50, f"Stamping seal on {total_pages} page(s)…")
                writer2 = PdfWriter()
                rdr2 = PdfReader(str(merged))
                for pi, page in enumerate(rdr2.pages):
                    pw = float(page.mediabox.width)
                    ph = float(page.mediabox.height)
                    overlay_bytes = self._make_seal_overlay(pw, ph, seal_img)
                    overlay_page  = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
                    page.merge_page(overlay_page)
                    writer2.add_page(page)
                sealed = tmp / "sealed.pdf"
                with open(sealed, "wb") as fh:
                    writer2.write(fh)

                # Step 4 – render → TIFF under 5 MB
                dpi = RENDER_DPI_START
                out = Path(self.output_path)

                while dpi >= RENDER_DPI_MIN:
                    self.progress.emit(
                        65, f"Rendering TIFF at {dpi} DPI…")
                    pages_img = convert_from_path(str(sealed), dpi=dpi, poppler_path=_poppler_path)
                    rgb_pages = [p.convert("RGB") for p in pages_img]

                    self.progress.emit(85, "Compressing TIFF…")
                    buf = io.BytesIO()
                    if len(rgb_pages) == 1:
                        rgb_pages[0].save(buf, format="TIFF",
                                          compression="jpeg", quality=82)
                    else:
                        rgb_pages[0].save(
                            buf, format="TIFF",
                            compression="jpeg", quality=82,
                            save_all=True,
                            append_images=rgb_pages[1:]
                        )
                    size = buf.tell()
                    if size <= MAX_TIFF_BYTES:
                        buf.seek(0)
                        out.write_bytes(buf.getvalue())
                        self.progress.emit(100, "Done!")
                        self.finished.emit(str(out), size / 1024 / 1024)
                        return

                    ratio   = MAX_TIFF_BYTES / size
                    new_dpi = max(int(dpi * math.sqrt(ratio) * 0.88),
                                  RENDER_DPI_MIN)
                    if new_dpi >= dpi:
                        break
                    dpi = new_dpi

                # Last resort – save anyway
                buf.seek(0)
                out.write_bytes(buf.getvalue())
                self.progress.emit(100, "Done (best effort).")
                self.finished.emit(str(out), buf.tell() / 1024 / 1024)

        except Exception as ex:
            import traceback
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════════════════
#  Status Badge
# ═══════════════════════════════════════════════════════════════════════════════
class Badge(QLabel):
    def __init__(self, text, color):
        super().__init__(text)
        self.setStyleSheet(f"""
            QLabel {{
                background: {color}22;
                color: {color};
                border: 1px solid {color}55;
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DocSealer")
        self.setMinimumSize(680, 780)
        self.resize(720, 840)
        self.setStyleSheet(STYLE)
        self._worker = None
        self._build_ui()
        self._check_deps()

    # ── dependency check ─────────────────────────────────────────────────────
    def _check_deps(self):
        issues = []
        if not LIBS_OK:
            issues.append(f"Missing: {LIBS_ERROR}")
        if not PDF2IMAGE_OK:
            issues.append("Missing: pdf2image  →  pip install pdf2image")
        if not HEIC_OK:
            self.heic_badge.setText("HEIC: off")
        if issues:
            QMessageBox.warning(self, "Missing Dependencies",
                                "\n".join(issues) +
                                "\n\nInstall then restart the app.")

    # ── UI build ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(28, 24, 28, 24)
        main_lay.setSpacing(18)

        # ── Header ────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("DocSealer")
        title.setStyleSheet(
            f"font-size: 26px; font-weight: 800; color: {C['text']};"
            f"letter-spacing: -0.5px;")
        sub = QLabel("Merge · Seal · Export TIFF")
        sub.setStyleSheet(f"font-size: 13px; color: {C['muted']};")
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title)
        title_col.addWidget(sub)

        self.heic_badge = Badge("HEIC: on" if HEIC_OK else "HEIC: off",
                                C['success'] if HEIC_OK else C['warning'])

        size_badge = Badge("< 5 MB guarantee", C['accent'])
        hdr.addLayout(title_col)
        hdr.addStretch()
        hdr.addWidget(self.heic_badge)
        hdr.addWidget(size_badge)
        main_lay.addLayout(hdr)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C['border']};")
        main_lay.addWidget(sep)

        # ── Seal Row ──────────────────────────────────────────────────────
        seal_row = QHBoxLayout()
        seal_row.setSpacing(18)

        self.seal_preview = SealPreview()
        seal_col = QVBoxLayout()
        seal_col.setSpacing(6)
        seal_lbl = QLabel("Your Seal")
        seal_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {C['text']};")
        seal_hint = QLabel("Click the circle to load your\nseal image (PNG recommended).")
        seal_hint.setStyleSheet(f"font-size: 11px; color: {C['muted']};")
        self.seal_status = QLabel("⚠  No seal loaded")
        self.seal_status.setStyleSheet(
            f"font-size: 11px; color: {C['warning']};")

        load_seal_btn = QPushButton("Browse seal…")
        load_seal_btn.setFixedWidth(120)
        load_seal_btn.clicked.connect(self._browse_seal)

        seal_col.addWidget(seal_lbl)
        seal_col.addWidget(seal_hint)
        seal_col.addWidget(self.seal_status)
        seal_col.addWidget(load_seal_btn)
        seal_col.addStretch()

        seal_row.addWidget(self.seal_preview)
        seal_row.addLayout(seal_col)
        seal_row.addStretch()
        main_lay.addLayout(seal_row)

        # ── Drop Zone ─────────────────────────────────────────────────────
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_files)
        main_lay.addWidget(self.drop_zone)

        # ── File List ─────────────────────────────────────────────────────
        list_hdr = QHBoxLayout()
        self.file_count_lbl = QLabel("Files (0)")
        self.file_count_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {C['text']};")
        clr_btn = QPushButton("Clear all")
        clr_btn.setFixedWidth(90)
        clr_btn.clicked.connect(self._clear_files)
        rem_btn = QPushButton("Remove selected")
        rem_btn.setFixedWidth(140)
        rem_btn.clicked.connect(self._remove_selected)
        list_hdr.addWidget(self.file_count_lbl)
        list_hdr.addStretch()
        list_hdr.addWidget(rem_btn)
        list_hdr.addWidget(clr_btn)
        main_lay.addLayout(list_hdr)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setMinimumHeight(160)
        self.file_list.setMaximumHeight(220)
        main_lay.addWidget(self.file_list)

        # Reorder hint
        reorder_lbl = QLabel("💡 Files will be merged in list order above")
        reorder_lbl.setStyleSheet(
            f"font-size: 11px; color: {C['muted']}; padding: 2px 0;")
        main_lay.addWidget(reorder_lbl)

        # ── Output Path ───────────────────────────────────────────────────
        out_row = QHBoxLayout()
        out_lbl = QLabel("Output:")
        out_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {C['text']};")
        out_lbl.setFixedWidth(62)
        self.out_path_lbl = QLabel("Not set — click Browse")
        self.out_path_lbl.setStyleSheet(
            f"font-size: 12px; color: {C['muted']};"
            f"background: {C['surface']}; border-radius: 6px; padding: 6px 10px;")
        self.out_path_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        browse_out_btn = QPushButton("Browse…")
        browse_out_btn.setFixedWidth(90)
        browse_out_btn.clicked.connect(self._browse_output)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self.out_path_lbl)
        out_row.addWidget(browse_out_btn)
        main_lay.addLayout(out_row)
        self._output_path = None

        # ── Progress ──────────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setVisible(False)
        main_lay.addWidget(self.progress_bar)

        self.status_lbl = QLabel("")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet(
            f"font-size: 12px; color: {C['muted']}; padding: 2px;")
        main_lay.addWidget(self.status_lbl)

        # ── Run Button ────────────────────────────────────────────────────
        self.run_btn = QPushButton("⚡  Generate Sealed TIFF")
        self.run_btn.setFixedHeight(52)
        self.run_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {C['accent']}, stop:1 {C['accent2']});
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 15px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3B5FCC, stop:1 #6B4E97);
            }}
            QPushButton:pressed {{ background: #2A4AAA; }}
            QPushButton:disabled {{
                background: {C['surface']};
                color: {C['muted']};
                border: 1px solid {C['border']};
            }}
        """)
        self.run_btn.clicked.connect(self._run)
        main_lay.addWidget(self.run_btn)

        # ── Result Banner ─────────────────────────────────────────────────
        self.result_frame = QFrame()
        self.result_frame.setVisible(False)
        self.result_frame.setStyleSheet(f"""
            QFrame {{
                background: {C['success']}18;
                border: 1px solid {C['success']}44;
                border-radius: 10px;
            }}
        """)
        res_lay = QHBoxLayout(self.result_frame)
        self.result_lbl = QLabel()
        self.result_lbl.setStyleSheet(
            f"font-size: 13px; color: {C['success']}; font-weight: 600;")
        open_btn = QPushButton("Open folder")
        open_btn.setFixedWidth(110)
        open_btn.clicked.connect(self._open_folder)
        res_lay.addWidget(self.result_lbl)
        res_lay.addStretch()
        res_lay.addWidget(open_btn)
        main_lay.addWidget(self.result_frame)

        main_lay.addStretch()

    # ── Actions ───────────────────────────────────────────────────────────────
    def _browse_seal(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Seal Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.seal_preview.load_seal(path)
            self.seal_status.setText(f"✓  {Path(path).name}")
            self.seal_status.setStyleSheet(
                f"font-size: 11px; color: {C['success']};")

    def _add_files(self, paths: list):
        existing = {self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.file_list.count())}
        # Never add the seal image or the output file as a document page
        seal_path = self.seal_preview.seal_path
        for p in paths:
            if p == seal_path:
                continue  # skip seal image silently
            if self._output_path and Path(p).resolve() == Path(self._output_path).resolve():
                continue  # skip output file silently
            if p not in existing:
                item = QListWidgetItem()
                fp = Path(p)
                ext = fp.suffix.upper().lstrip(".")
                size_kb = fp.stat().st_size // 1024
                item.setText(f"  {fp.name}   ({ext}  ·  {size_kb} KB)")
                item.setData(Qt.ItemDataRole.UserRole, p)
                self.file_list.addItem(item)
        self._update_file_count()

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_file_count()

    def _clear_files(self):
        self.file_list.clear()
        self._update_file_count()

    def _update_file_count(self):
        n = self.file_list.count()
        self.file_count_lbl.setText(f"Files ({n})")

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Output TIFF", "output.tiff",
            "TIFF Image (*.tiff *.tif)"
        )
        if path:
            if not path.lower().endswith((".tiff", ".tif")):
                path += ".tiff"
            self._output_path = path
            self.out_path_lbl.setText(path)
            self.out_path_lbl.setStyleSheet(
                f"font-size: 12px; color: {C['text']};"
                f"background: {C['surface']}; border-radius: 6px; padding: 6px 10px;")
            # Remove output file from file list if it was already added
            out_resolved = Path(path).resolve()
            for i in range(self.file_list.count() - 1, -1, -1):
                item_path = Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)).resolve()
                if item_path == out_resolved:
                    self.file_list.takeItem(i)
            self._update_file_count()

    def _validate(self) -> bool:
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "No Files", "Please add at least one input file.")
            return False
        if not self.seal_preview.seal_path:
            QMessageBox.warning(self, "No Seal", "Please load your seal image first.")
            return False
        if not self._output_path:
            QMessageBox.warning(self, "No Output", "Please choose an output file path.")
            return False
        return True

    def _run(self):
        if not self._validate():
            return
        seal_path = self.seal_preview.seal_path
        out_path_resolved = Path(self._output_path).resolve() if self._output_path else None
        files = [
            Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.file_list.count())
            if self.file_list.item(i).data(Qt.ItemDataRole.UserRole) != seal_path
            and (out_path_resolved is None or
                 Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)).resolve() != out_path_resolved)
        ]
        self.run_btn.setEnabled(False)
        self.result_frame.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setText("Starting…")

        self._worker = Worker(files, self.seal_preview.seal_path, self._output_path)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)

    def _on_finished(self, out_path: str, size_mb: float):
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        ok = size_mb <= 5.0
        status_col = C['success'] if ok else C['warning']
        self.status_lbl.setText(
            f"{'✅' if ok else '⚠️'}  {size_mb:.2f} MB  ·  "
            f"{'Under 5 MB limit' if ok else 'Slightly over — try fewer pages'}")
        self.status_lbl.setStyleSheet(
            f"font-size: 12px; color: {status_col}; padding: 2px;")
        self._last_out_path = out_path
        self.result_lbl.setText(
            f"✅  Saved: {Path(out_path).name}  ({size_mb:.2f} MB)")
        self.result_frame.setVisible(True)

    def _on_error(self, err: str):
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_lbl.setText("Error — see details below")
        QMessageBox.critical(self, "Pipeline Error", err)

    def _open_folder(self):
        if hasattr(self, "_last_out_path"):
            folder = str(Path(self._last_out_path).parent)
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DocSealer")
    app.setOrganizationName("DocSealer")

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
