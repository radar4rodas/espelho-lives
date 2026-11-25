// Roda DENTRO da página do Gerenciador de LIVE do TikTok Shop (mundo da página, antes dos scripts dela).
//
// O "microfone" da live é o CABLE Output, que leva o som pronto da live da Shopee (voz, música, produto).
// Por padrão o Chrome trata o microfone como numa chamada de vídeo: redução de ruído, controle
// automático de volume e cancelamento de eco. Com o som de uma live isso deixa a voz baixa e corta
// trechos. Aqui esses três tratamentos são desligados, para o som chegar ao TikTok como sai do OBS.

(() => {
  if (window.__espelhoAudioPuro) return;
  window.__espelhoAudioPuro = true;

  const PURO = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
  // nomes antigos que alguns sites ainda usam (Chrome)
  const LEGADO = ["googEchoCancellation", "googNoiseSuppression", "googAutoGainControl",
                  "googHighpassFilter", "googTypingNoiseDetection", "googNoiseSuppression2",
                  "googEchoCancellation2", "googAutoGainControl2"];

  function audioPuro(audio) {
    if (!audio) return audio;                       // sem áudio: não mexe
    const a = audio === true ? {} : { ...audio };
    Object.assign(a, PURO);
    if (Array.isArray(a.advanced)) {
      a.advanced = a.advanced.map((c) => {
        const r = { ...c };
        for (const k of [...Object.keys(PURO), ...LEGADO]) if (k in r) r[k] = false;
        return r;
      });
    }
    if (a.mandatory && typeof a.mandatory === "object") {
      a.mandatory = { ...a.mandatory };
      for (const k of LEGADO) if (k in a.mandatory) a.mandatory[k] = false;
    }
    if (Array.isArray(a.optional)) {
      a.optional = a.optional.map((c) => {
        const r = { ...c };
        for (const k of LEGADO) if (k in r) r[k] = false;
        return r;
      });
    }
    return a;
  }
  function pedidoPuro(pedido) {
    if (!pedido || typeof pedido !== "object" || !pedido.audio) return pedido;
    return { ...pedido, audio: audioPuro(pedido.audio) };
  }
  globalThis.__espelhoPedidoPuro = pedidoPuro;     // para teste

  const md = navigator.mediaDevices;
  if (md && typeof md.getUserMedia === "function") {
    const original = md.getUserMedia.bind(md);
    md.getUserMedia = function (pedido) { return original(pedidoPuro(pedido)); };
  }
  // API antiga (callbacks)
  for (const nome of ["getUserMedia", "webkitGetUserMedia"]) {
    const antigo = navigator[nome];
    if (typeof antigo === "function") {
      navigator[nome] = function (pedido, ok, erro) { return antigo.call(navigator, pedidoPuro(pedido), ok, erro); };
    }
  }
  // se a página tentar religar depois, numa trilha de áudio já aberta
  if (window.MediaStreamTrack && MediaStreamTrack.prototype.applyConstraints) {
    const aplicar = MediaStreamTrack.prototype.applyConstraints;
    MediaStreamTrack.prototype.applyConstraints = function (c) {
      if (this.kind === "audio") c = { ...(c || {}), ...PURO };
      return aplicar.call(this, c);
    };
  }
})();
