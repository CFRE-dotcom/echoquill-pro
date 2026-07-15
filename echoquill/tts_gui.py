"""Read aloud - paste text or open a document and hear it spoken, or save it
as an MP3. Pro feature, powered by your own ElevenLabs account.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import theme, helptip, tts
from .media_gui import narration_dir

READ_HELP = (
    "Read aloud (Text-to-speech)\n\n"
    "• Paste text, or click 'Open a document…' (.txt, .md, .docx, .pdf).\n"
    "• Pick a voice, then Play to hear it, or Save as MP3 to keep the audio.\n"
    "• Audio is saved in your EchoQuill\\Narration folder.\n\n"
    "This uses YOUR ElevenLabs account. Paste your API key once and it's kept "
    "safely in Windows Credential Manager. Get a key at elevenlabs.io → Profile."
)


class ReadAloudWindow:
    def __init__(self, root, cfg):
        self.cfg = cfg
        self._voice_id = cfg.get("tts_voice_id", "") or tts.DEFAULT_VOICE
        self._voices = []            # [(name, id)]
        self._busy = False
        self._play_wav = None

        self.win = tk.Toplevel(root)
        self.win.title("EchoQuill — Read aloud")
        self.win.geometry("640x560")
        self.win.minsize(540, 480)
        self.win.attributes("-topmost", True)
        theme.apply(self.win)

        # ---- bottom action bar first (never pushed off-screen) ----
        bar = ttk.Frame(self.win)
        bar.pack(side="bottom", fill="x", padx=18, pady=(2, 12))
        self.play_btn = ttk.Button(bar, text="▶ Play", style="Accent.TButton",
                                   command=self._play)
        self.play_btn.pack(side="left")
        helptip.tip(self.play_btn, "Read the text aloud.")
        self.stop_btn = ttk.Button(bar, text="■ Stop", command=self._stop)
        self.stop_btn.pack(side="left", padx=8)
        helptip.tip(self.stop_btn, "Stop playback.")
        _save = ttk.Button(bar, text="Save as MP3…", command=self._save)
        _save.pack(side="left")
        helptip.tip(_save, "Generate the audio and save it as an MP3 file.")
        _open = ttk.Button(bar, text="Open folder",
                           command=lambda: self._open_folder())
        _open.pack(side="left", padx=8)
        helptip.tip(_open, "Open your EchoQuill\\Narration folder.")
        _clear = ttk.Button(bar, text="Clear", command=self._clear)
        _clear.pack(side="left")
        helptip.tip(_clear, "Clear the text box.")
        self.status = ttk.Label(bar, text="", style="Dim.TLabel")
        self.status.pack(side="left", padx=12)

        # ---- title ----
        trow = ttk.Frame(self.win)
        trow.pack(anchor="w", padx=18, pady=(14, 2))
        ttk.Label(trow, text="Read aloud", style="Title.TLabel").pack(side="left")
        helptip.attach(self.win, trow, "Read aloud — help", READ_HELP).pack(
            side="left", padx=8)
        ttk.Label(self.win, style="Dim.TLabel", wraplength=590, text=(
            "Paste text or open a document, pick a voice, then Play or Save as "
            "MP3. Uses your ElevenLabs account.")).pack(anchor="w", padx=18)

        # ---- API key row ----
        krow = ttk.Frame(self.win)
        krow.pack(fill="x", padx=18, pady=(10, 2))
        ttk.Label(krow, text="ElevenLabs API key:").pack(side="left")
        self.key_var = tk.StringVar(value=cfg.get("elevenlabs_api_key", ""))
        self.key_entry = ttk.Entry(krow, textvariable=self.key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=2)
        _ks = ttk.Button(krow, text="Save key", command=self._save_key)
        _ks.pack(side="left")
        helptip.tip(_ks, "Store your ElevenLabs API key (kept in Windows "
                    "Credential Manager) and load your voices.")

        # ---- voice + open-document row ----
        vrow = ttk.Frame(self.win)
        vrow.pack(fill="x", padx=18, pady=(6, 2))
        ttk.Label(vrow, text="Voice:").pack(side="left")
        self.voice_var = tk.StringVar(value="(add key, then Load voices)")
        self.voice_menu = ttk.OptionMenu(vrow, self.voice_var,
                                         "(add key, then Load voices)")
        self.voice_menu.configure(width=24)
        self.voice_menu.pack(side="left", padx=(6, 6))
        _lv = ttk.Button(vrow, text="Load voices", command=self._load_voices)
        _lv.pack(side="left")
        helptip.tip(_lv, "Fetch the voices on your ElevenLabs account.")
        _od = ttk.Button(vrow, text="Open a document…", command=self._open_doc)
        _od.pack(side="right")
        helptip.tip(_od, "Load a .txt, .md, .docx or .pdf to read aloud.")

        ttk.Label(self.win, style="Dim.TLabel",
                  text="Text to read:").pack(anchor="w", padx=18, pady=(8, 0))
        self.box = theme.dark_text(self.win, wrap="word")
        self.box.pack(fill="both", expand=True, padx=18, pady=(2, 6))

        if self.cfg.get("elevenlabs_api_key"):
            self._load_voices()

    # ---------- helpers ----------

    def _set_status(self, text):
        self.win.after(0, lambda: self.status.configure(text=text))

    def _error(self, where, e):
        tts.log_error(where, e)
        msg = str(e)
        self._set_status(msg)
        self.win.after(0, lambda: messagebox.showerror(
            "Read aloud", msg + "\n\n(Details saved to tts_error.log in your "
            "EchoQuill settings folder.)"))

    def _open_folder(self):
        try:
            os.startfile(narration_dir(self.cfg))
        except Exception:
            self._set_status("Could not open folder")

    def _clear(self):
        self.box.delete("1.0", "end")
        self.status.configure(text="")

    def _save_key(self):
        from . import config as _cfg
        self.cfg["elevenlabs_api_key"] = self.key_var.get().strip()
        try:
            _cfg.save(self.cfg)
        except Exception:
            pass
        self._set_status("Key saved ✓")
        self._load_voices()

    def _open_doc(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="Open a document to read",
            filetypes=[("Documents", "*.txt *.md *.docx *.pdf"),
                       ("All files", "*.*")])
        if not path:
            return
        self._set_status("Reading document…")

        def run():
            try:
                text = tts.read_document(path)
            except Exception as e:
                self._set_status(str(e)); return

            def show():
                self.box.delete("1.0", "end")
                self.box.insert("1.0", text)
                self.status.configure(
                    text=f"Loaded {os.path.basename(path)} ✓")
            self.win.after(0, show)
        threading.Thread(target=run, daemon=True).start()

    def _load_voices(self):
        self.cfg["elevenlabs_api_key"] = self.key_var.get().strip()
        if not self.cfg["elevenlabs_api_key"]:
            self._set_status("Add your ElevenLabs API key first.")
            return
        self._set_status("Loading voices…")

        def run():
            try:
                voices = tts.list_voices(self.cfg)
            except Exception as e:
                self._set_status(str(e)); return
            self.win.after(0, lambda: self._fill_voices(voices))
        threading.Thread(target=run, daemon=True).start()

    def _fill_voices(self, voices):
        self._voices = voices
        menu = self.voice_menu["menu"]
        menu.delete(0, "end")
        if not voices:
            self.voice_var.set("(no voices found)")
            self._set_status("No voices on this account.")
            return
        for name, vid in voices:
            menu.add_command(label=name,
                             command=lambda n=name, i=vid: self._pick_voice(n, i))
        # keep the previously chosen voice if it's still there
        cur = next((n for n, i in voices if i == self._voice_id), None)
        if cur:
            self.voice_var.set(cur)
        else:
            self._pick_voice(voices[0][0], voices[0][1])
        self._set_status(f"Loaded {len(voices)} voice"
                         f"{'s' if len(voices) != 1 else ''} ✓")

    def _pick_voice(self, name, vid):
        self.voice_var.set(name)
        self._voice_id = vid
        self.cfg["tts_voice_id"] = vid

    # ---------- play / save ----------

    def _guard(self):
        if self._busy:
            self._set_status("Still working on the last one…")
            return False
        if not self.key_var.get().strip():
            self._set_status("Add your ElevenLabs API key first.")
            return False
        if not self.box.get("1.0", "end").strip():
            self._set_status("Nothing to read - paste or open some text.")
            return False
        self.cfg["elevenlabs_api_key"] = self.key_var.get().strip()
        return True

    def _play(self):
        if not self._guard():
            return
        text = self.box.get("1.0", "end").strip()
        self._busy = True
        self.play_btn.configure(state="disabled")
        self._set_status("Generating audio…")

        def run():
            import tempfile
            try:
                pcm = tts.synth_pcm(text, self.cfg, self._voice_id,
                                    status_cb=self._set_status)
                wav = os.path.join(tempfile.gettempdir(),
                                   "echoquill_readaloud.wav")
                tts.pcm_to_wav(pcm, wav)
                self._play_wav = wav
                import winsound
                winsound.PlaySound(
                    wav, winsound.SND_FILENAME | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT)
                self._set_status("Playing ▶")
            except Exception as e:
                self._error("play", e)
            finally:
                self._busy = False
                self.win.after(0, lambda: self.play_btn.configure(state="normal"))
        threading.Thread(target=run, daemon=True).start()

    def _stop(self):
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
            self._set_status("Stopped.")
        except Exception:
            pass

    def _save(self):
        if not self._guard():
            return
        text = self.box.get("1.0", "end").strip()
        folder = narration_dir(self.cfg)
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Save narration as MP3",
            initialdir=folder, defaultextension=".mp3",
            filetypes=[("MP3 audio", "*.mp3")])
        if not path:
            return
        self._busy = True
        self._set_status("Generating audio…")

        def run():
            try:
                tts.synthesize_to_mp3(text, self.cfg, self._voice_id, path,
                                      status_cb=self._set_status)
                self._set_status(f"Saved ✓  {os.path.basename(path)}")
            except Exception as e:
                self._error("save", e)
            finally:
                self._busy = False
        threading.Thread(target=run, daemon=True).start()
