// Roda DENTRO da página do Gerenciador de LIVE do TikTok Shop (mundo da página, antes dos scripts dela).
//
// O Gerenciador (na web) não tem botão de pausar. Para pausar a live no TikTok quando a live da Shopee
// pausa, a extensão desliga a câmera e o som DA CONEXÃO da live (RTCRtpSender.replaceTrack(null)): a
// conexão continua aberta, nada é fechado, só para de chegar imagem e som ao TikTok. Para retomar,
// as mesmas trilhas voltam para a conexão (sem pedir a câmera de novo, sem recarregar a página).
//
// Conversa com content_tiktok.js por window.postMessage. Quantas conexões com envio existem fica
// em <html data-espelho-envio="N">, que o content script consegue ler.

(() => {
  if (window.__espelhoPausa) return;
  window.__espelhoPausa = true;

  const conexoes = new Set();                 // RTCPeerConnection abertas pela página
  const guardadas = new Map();                // sender -> trilha tirada na pausa
  let pausado = false;

  function envios() {
    const r = [];
    for (const pc of conexoes) {
      if (pc.connectionState === "closed" || pc.signalingState === "closed") { conexoes.delete(pc); continue; }
      for (const s of pc.getSenders()) if (s.track || guardadas.has(s)) r.push(s);
    }
    return r;
  }
  function marcar() {
    try {
      document.documentElement.dataset.espelhoEnvio = String(envios().length);
      document.documentElement.dataset.espelhoPausado = pausado ? "1" : "0";
    } catch (_) {}
  }

  const Original = window.RTCPeerConnection;
  if (typeof Original === "function") {
    const Embrulho = function (...args) {
      const pc = new Original(...args);
      conexoes.add(pc);
      pc.addEventListener("connectionstatechange", marcar);
      setTimeout(marcar, 0);
      return pc;
    };
    Embrulho.prototype = Original.prototype;
    Object.setPrototypeOf(Embrulho, Original);   // métodos estáticos (generateCertificate)
    window.RTCPeerConnection = Embrulho;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = Embrulho;
    // trilhas novas entram depois de criar a conexão
    for (const nome of ["addTrack", "addTransceiver", "setLocalDescription"]) {
      const f = Original.prototype[nome];
      if (typeof f === "function") {
        Original.prototype[nome] = function (...a) { conexoes.add(this); const r = f.apply(this, a); setTimeout(marcar, 0); return r; };
      }
    }
  }

  async function pausar() {
    let n = 0;
    for (const s of envios()) {
      if (!s.track) continue;
      guardadas.set(s, s.track);
      try { await s.replaceTrack(null); n++; } catch (_) { guardadas.delete(s); }
    }
    pausado = n > 0 || guardadas.size > 0;
    marcar();
    return { ok: pausado, trilhas: n, ja: n === 0 && guardadas.size > 0 };
  }
  async function retomar() {
    let n = 0;
    for (const [s, t] of [...guardadas]) {
      try { if (t.readyState === "live") { await s.replaceTrack(t); n++; } } catch (_) {}
      guardadas.delete(s);
    }
    pausado = false;
    marcar();
    return { ok: n > 0, trilhas: n };
  }

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.espelhoPedido === undefined) return;
    const { espelhoPedido: pedido, id } = ev.data;
    let r = { ok: false };
    try {
      if (pedido === "pausar_envio") r = await pausar();
      else if (pedido === "retomar_envio") r = await retomar();
      else if (pedido === "estado_envio") r = { ok: true, envios: envios().length, pausado };
    } catch (e) { r = { ok: false, erro: String(e) }; }
    window.postMessage({ espelhoResposta: id, ...r }, "*");
  });
  setInterval(marcar, 5000);
})();
