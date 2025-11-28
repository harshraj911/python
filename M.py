# Decompiled with PyLingual (https://pylingual.io)
import sys
import requests
import json
import threading
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QDropEvent
import keyboard
import re

GEMINI_API_KEY = 'AIzaSyDkqxHtzieOTvZc9GfPnzXtjXtFMsW1_pk'
GEMINI_API_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}'

# Utility to remove comments and remove build/exe instructions from responses
def clean_answer(text: str) -> str:
    if not text:
        return text

    # Remove C-style block comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

    cleaned_lines = []
    build_keywords = [
        'pyinstaller', 'cx_Freeze', 'cx_Freeze', 'nuitka', 'build', 'exe', 'setup.py', 'freeze', 'compile', 'pip install pyinstaller'
    ]

    for line in text.splitlines():
        stripped = line.strip()
        # Skip empty lines only if original line was empty
        if stripped == '':
            cleaned_lines.append('')
            continue

        # Drop entire lines that are clearly comments (starting with # or //)
        if stripped.startswith('#') or stripped.startswith('//'):
            continue

        # Drop lines that contain build/exe keywords (to avoid exe-building instructions)
        lower = line.lower()
        if any(kw in lower for kw in build_keywords):
            continue

        # Remove inline '//' comments
        if '//' in line:
            line = line.split('//', 1)[0].rstrip()

        # Remove simple inline Python comments introduced by ' #'
        # NOTE: This is a heuristic and may remove comments in some strings; it's intentionally conservative
        if ' #' in line:
            # split on ' #' and keep left side
            line = line.split(' #', 1)[0].rstrip()

        # Also remove trailing '#' comments if the line begins with code and contains ' #'
        # If a line still contains an isolated '#' at start after left-strip, remove it
        if line.lstrip().startswith('#'):
            continue

        if line.strip() != '':
            cleaned_lines.append(line.rstrip())

    # Rejoin lines and strip leading/trailing whitespace
    cleaned = '\n'.join(cleaned_lines).strip()

    # Additional pass: if entire response is prose (no code indicators) remove lines that start with common comment prefixes
    # (already largely handled above)

    return cleaned

class GeminiThread(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, user_input):
        super().__init__()
        self.user_input = user_input

    def run(self):
        import os
        # Prefer environment variable over hard-coded key
        api_key = os.getenv('GEMINI_API_KEY') or GEMINI_API_KEY or ''
        if not api_key or api_key == 'YOUR_API_KEY_HERE':
            self.result_ready.emit('Error: No API key set. Set GEMINI_API_KEY environment variable or update GEMINI_API_KEY in the script.')
            return

        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}'
        headers = {'Content-Type': 'application/json'}
        prompt = self.user_input or ''
        payload = {'contents': [{'parts': [{'text': prompt}]}]}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
        except requests.exceptions.RequestException as e:
            self.result_ready.emit(f'Network error when calling API: {e}')
            return

        if resp.status_code != 200:
            body = resp.text
            # try to shorten very long bodies
            if len(body) > 1500:
                body = body[:1500] + '... (truncated)'
            self.result_ready.emit(f'API returned HTTP {resp.status_code}: {body}')
            return

        try:
            response_json = resp.json()
        except ValueError:
            self.result_ready.emit('API returned non-JSON response.')
            return

        # Try common response shapes
        answer = None
        try:
            candidates = response_json.get('candidates')
            if isinstance(candidates, list) and candidates:
                first = candidates[0]
                content = first.get('content')
                # content may be a dict (with 'parts') or a list of content blocks
                parts = []
                if isinstance(content, dict):
                    parts = content.get('parts') or []
                elif isinstance(content, list) and content:
                    if isinstance(content[0], dict):
                        parts = content[0].get('parts') or []
                if isinstance(parts, list) and parts:
                    answer = parts[0].get('text')
        except Exception:
            answer = None

        if not answer:
            # alternate shapes
            if 'output' in response_json:
                out = response_json['output']
                if isinstance(out, list):
                    for item in out:
                        if isinstance(item, dict) and 'content' in item and isinstance(item['content'], list):
                            for c in item['content']:
                                if isinstance(c, dict) and 'text' in c:
                                    answer = c['text']
                                    break
                            if answer:
                                break

        if not answer:
            if isinstance(response_json.get('text'), str):
                answer = response_json.get('text')

        if not answer:
            try:
                raw = json.dumps(response_json)
                if len(raw) > 2000:
                    raw = raw[:2000] + '... (truncated)'
                answer = "Unrecognized API response shape. Raw JSON (truncated):\n" + raw
            except Exception:
                answer = 'Unrecognized API response and failed to stringify it.'

        cleaned = clean_answer(answer if answer is not None else '')
        self.result_ready.emit(cleaned)

class GeminiApp(QWidget):

    def __init__(self):
        super().__init__()
        self.init_ui()
        self.setup_window()
        self.start_hotkey_thread()

    def setup_window(self):
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.BypassWindowManagerHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_X11DoNotAcceptFocus)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAcceptDrops(True)
        self.setWindowOpacity(0.95)
        self.resize(400, 400)
        screen_rect = QApplication.primaryScreen().availableGeometry()
        self.move(0, screen_rect.height() - self.height())
        self.hide()
        self.setEnabled(False)

    def init_ui(self):
        layout = QVBoxLayout()
        self.drop_label = QLabel('Drop text here to query Gemini')
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.setStyleSheet('\n            QLabel {\n                border: 2px dashed #aaa;\n                padding: 20px;\n                color: #777;\n            }\n        ')
        layout.addWidget(self.drop_label)
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        layout.addWidget(self.output_text)
        btn_layout = QHBoxLayout()
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.hide_window)
        btn_layout.addWidget(self.close_button)
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        dropped_text = event.mimeData().text()
        self.output_text.setText('Thinking...')
        self.thread = GeminiThread(dropped_text.strip())
        self.thread.result_ready.connect(self.display_result)
        self.thread.start()

    def display_result(self, answer):
        # If the cleaned answer looks like Python source code, ensure we do not convert it to instructions about building an exe.
        # Heuristic for code: presence of 'def ', 'import ', 'class ' or 'print(' indicates code; already build-related lines are removed in clean_answer.
        if any(token in answer for token in ('def ', 'import ', 'class ', 'print(', 'if __name__ ==')):
            # display as-is (cleaned)
            self.output_text.setPlainText(answer)
        else:
            # For prose answers, also ensure there are no comment-like lines left
            self.output_text.setPlainText(answer)

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.setEnabled(True)

    def hide_window(self):
        self.setEnabled(False)
        self.hide()

    def start_hotkey_thread(self):

        def hotkey_listener():
            keyboard.add_hotkey('alt+s', lambda: QTimer.singleShot(0, self.toggle_visibility))
            keyboard.wait()
        thread = threading.Thread(target=hotkey_listener, daemon=True)
        thread.start()


def main():
    app = QApplication(sys.argv)
    window = GeminiApp()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
