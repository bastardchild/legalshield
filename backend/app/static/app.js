// Alpine.js components — LegalShield Agent (dark mode)

document.addEventListener('alpine:init', () => {

  // ── Upload page ──────────────────────────────────────────
  Alpine.data('uploadApp', () => ({
    fileName: '',
    dragOver: false,
    loading: false,
    toast: false,
    toastMsg: '',
    toastType: 'ok',

    onFileChange(e) {
      const f = e.target.files[0];
      if (f) this.fileName = f.name;
    },
    onDrop(e) {
      this.dragOver = false;
      const f = e.dataTransfer.files[0];
      if (f && (f.type === 'application/pdf' || f.type === 'text/plain' || f.name.endsWith('.txt'))) {
        this.fileName = f.name;
        const dt = new DataTransfer();
        dt.items.add(f);
        this.$refs.fileInput.files = dt.files;
      } else {
        this.showToast('Hanya file PDF atau TXT yang diterima.', 'err');
      }
    },
    clearFile() {
      this.fileName = '';
      this.$refs.fileInput.value = '';
    },
    onUploadDone(e) {
      this.loading = false;
      const xhr = e.detail.xhr;
      if (xhr.status === 200) {
        try {
          const data = JSON.parse(xhr.responseText);
          if (data.id) { window.location.href = `/contracts/${data.id}`; return; }
        } catch {}
      }
      try {
        const err = JSON.parse(xhr.responseText);
        this.showToast(err.detail || 'Upload gagal.', 'err');
      } catch {
        this.showToast('Upload gagal. Coba lagi.', 'err');
      }
    },
    showToast(msg, type = 'ok') {
      this.toastMsg = msg;
      this.toastType = type;
      this.toast = true;
      setTimeout(() => { this.toast = false; }, 3800);
    }
  }));

  // ── Result page ──────────────────────────────────────────
  Alpine.data('resultApp', (contractId) => ({
    contractId,
    toast: false,
    toastMsg: '',
    toastType: 'ok',
    _stopPolling: false,

    init() {
      document.body.addEventListener('htmx:afterSwap', (evt) => {
        if (this._stopPolling) return;
        const text = evt.detail.target?.innerText || '';
        if (text.includes('selesai') || text.includes('gagal') || text.includes('Counter-Draft') || text.includes('counter_draft')) {
          this._stopPolling = true;
          ['status-area', 'result-inner'].forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.removeAttribute('hx-trigger'); htmx.process(el); }
          });
        }
      });
    },
    showToast(msg, type = 'ok') {
      this.toastMsg = msg; this.toastType = type; this.toast = true;
      setTimeout(() => { this.toast = false; }, 3800);
    }
  }));

  // ── Send draft ───────────────────────────────────────────
  Alpine.data('sendApp', (contractId) => ({
    contractId,
    email: '',
    sending: false,
    sent: false,
    sendError: '',

    async sendDraft() {
      this.sending = true; this.sent = false; this.sendError = '';
      try {
        const r = await fetch(`/api/contracts/${this.contractId}/send`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recipient_email: this.email || null }),
        });
        if (r.ok) { this.sent = true; }
        else { const e = await r.json(); this.sendError = e.detail || 'Gagal mengirim draft.'; }
      } catch (e) {
        this.sendError = 'Network error: ' + e.message;
      } finally {
        this.sending = false;
      }
    }
  }));
});

