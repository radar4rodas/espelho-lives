// Roda no Gerenciador de LIVE do TikTok Shop (shop.tiktok.com), inclusive em quadros embutidos.
// - Conta para a extensão se a live está no ar e se existe botão de pausar.
// - Quando o painel manda: pausa, retoma ou encerra a live (clica no botão e confirma a janelinha).
// - "diagnóstico": lista os botões visíveis, para ajustar os textos se o TikTok mudar a página.

if (!globalThis.__espelhoLiveCarregado) {
globalThis.__espelhoLiveCarregado = true;

const RE_ENCERRAR = /^\s*(encerrar|finalizar|terminar)(\s+(a\s+)?live)?\s*$|^\s*end(\s+(the\s+)?live)?\s*$/i;
const RE_INICIAR = /^\s*(iniciar|começar|comecar)\s+(a\s+)?live\s*$|^\s*go\s+live\s*$/i;
const RE_PAUSAR = /^\s*pausar(\s+(a\s+)?live)?\s*$|^\s*pause(\s+live)?\s*$/i;
const RE_RETOMAR = /^\s*(retomar|continuar|voltar\s+ao\s+vivo)(\s+(a\s+)?live)?\s*$|^\s*resume(\s+live)?\s*$/i;
const RE_CONF = /^\s*(confirmar|encerrar|finalizar|terminar|pausar|retomar|continuar|ok|sim|confirm|end|pause|resume)\b/i;
const RE_CANC = /cancel|voltar\b|não|nao|agora não/i;
const SEL_CLIQUE = "button,[role=button],a,span,div";
const SEL_DIALOGO = "[role=dialog],[role=alertdialog],[class*=modal],[class*=Modal],[class*=dialog]," +
                    "[class*=Dialog],[class*=popup],[class*=confirm],[class*=popover]";

function visivel(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
}
// elementos visíveis cujo texto inteiro bate, ficando só com os mais "internos"
function achar(re, seletor = SEL_CLIQUE) {
  const els = [...document.querySelectorAll(seletor)].filter(e => re.test((e.textContent || "").trim()) && visivel(e));
  return els.filter(e => ![...e.children].some(c => re.test((c.textContent || "").trim())));
}
const clicavel = (el) => el.closest("button,[role=button],a") || el;
const desativado = (el) => el.disabled || el.getAttribute("aria-disabled") === "true" ||
                           /disabled/i.test(el.className || "");

// No ar, o Gerenciador mostra o contador (00:14:41) e, ao lado, o ícone ⏻ (classe "im_close_chat")
// que encerra a live. Não há texto "Encerrar", então procuramos o ícone.
const RE_CONTADOR = /^\d{1,2}:\d{2}:\d{2}$/;
function iconeEncerrar() {
  for (const svg of document.querySelectorAll("svg.arco-icon-im_close_chat, svg[class*='im_close_chat']")) {
    const botao = svg.closest(".cursor-pointer,[role=button],button") || svg.parentElement;
    if (botao && visivel(botao)) return botao;
  }
  return null;
}
function contadorVisivel() {
  for (const el of document.querySelectorAll("div.w-50, div[class*='text-body-s-medium']")) {
    if (RE_CONTADOR.test((el.textContent || "").trim()) && visivel(el)) return el;
  }
  return null;
}

function situacao() {
  if (iconeEncerrar() || contadorVisivel()) return "ao_vivo";
  if (achar(RE_ENCERRAR).length || achar(RE_PAUSAR).length || achar(RE_RETOMAR).length) return "ao_vivo";
  const ini = achar(RE_INICIAR).map(clicavel);
  if (ini.length) return "preparada";
  return "outra";
}
function estado() {
  return { tipo: "estado", site: "tiktok", situacao: situacao(), tem_pausa: achar(RE_PAUSAR).length > 0,
           titulo: document.title, principal: window.top === window };
}
globalThis.__espelhoEstado = estado;

let ultimo = "";
function reportar(forcar) {
  const e = estado();
  const k = e.situacao + "|" + e.tem_pausa;
  if (!forcar && k === ultimo) return;
  ultimo = k;
  try { chrome.runtime.sendMessage(e).catch(() => {}); } catch (_) {}
}
let espera = null;
new MutationObserver(() => {
  if (espera) return;
  espera = setTimeout(() => { espera = null; reportar(false); }, 400);
}).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
setInterval(() => reportar(true), 15000);
reportar(true);

// Espera uma condição usando MutationObserver (funciona mesmo com a aba em segundo plano).
function esperar(cond, ms, aCada) {
  return new Promise((resolve) => {
    const v = cond();
    if (v) return resolve(v);
    let fim = false;
    const obs = new MutationObserver(() => {
      if (fim) return;
      if (aCada) aCada();
      const r = cond();
      if (r) { fim = true; obs.disconnect(); clearTimeout(t); resolve(r); }
    });
    obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
    const t = setTimeout(() => { fim = true; obs.disconnect(); resolve(cond() || null); }, ms);
  });
}

function acharConfirmar(original, reAlvo) {
  for (const d of document.querySelectorAll(SEL_DIALOGO)) {
    if (!visivel(d)) continue;
    for (const b of d.querySelectorAll("button,[role=button]")) {
      const t = (b.textContent || "").trim();
      if (b !== original && visivel(b) && !desativado(b) && RE_CONF.test(t) && !RE_CANC.test(t)) return b;
    }
  }
  // janelinha do tipo "arco" (a que o TikTok usa): o botão principal que não é "Cancelar"
  for (const d of document.querySelectorAll(".arco-modal,.arco-popconfirm,.arco-popover-content")) {
    if (!visivel(d)) continue;
    const bs = [...d.querySelectorAll(".arco-btn-primary,.arco-btn-status-danger")]
      .filter(b => b !== original && visivel(b) && !desativado(b) && !RE_CANC.test((b.textContent || "").trim()));
    if (bs.length) return bs[bs.length - 1];
  }
  if (!reAlvo) return null;
  // janelinha com o mesmo texto do botão: o último visível é o dela
  const outros = achar(reAlvo).map(clicavel).filter(b => b !== original);
  return outros.length ? outros[outros.length - 1] : null;
}

// Encerrar pelo ícone ⏻: clica, confirma a janelinha e espera o contador sumir.
async function encerrarPeloIcone() {
  const t0 = performance.now();
  const alvo = iconeEncerrar();
  if (!alvo) return null;
  alvo.click();
  let confirmou = false, textoConf = "";
  const conf = await esperar(() => acharConfirmar(alvo, null) || (situacao() !== "ao_vivo" ? "saiu" : null), 5000);
  if (conf && conf !== "saiu") { textoConf = (conf.textContent || "").trim(); clicavel(conf).click(); confirmou = true; }
  const saiu = await esperar(() => (situacao() !== "ao_vivo" ? true : null), 10000, () => {
    if (!confirmou) {
      const c = acharConfirmar(alvo, null);
      if (c) { textoConf = (c.textContent || "").trim(); clicavel(c).click(); confirmou = true; }
    }
  });
  const segundos = Math.round((performance.now() - t0) / 100) / 10;
  if (saiu) return { ok: true, segundos, detalhe: "Live encerrada no Gerenciador de LIVE" +
                     (textoConf ? ` (confirmei em '${textoConf}').` : ".") };
  return { ok: false, segundos, detalhe: confirmou
    ? `Cliquei no ⏻ e em '${textoConf}', mas a live continua no ar. Confira a aba do TikTok.`
    : "Cliquei no ⏻, mas não achei o botão de confirmar na janelinha. Encerre na aba do TikTok.",
    botoes: diagnostico() };
}

// clica no botão, confirma a janelinha (se houver) e espera o botão sumir
async function clicarEConfirmar(reAlvo, nome) {
  const t0 = performance.now();
  const alvo = achar(reAlvo).map(clicavel).find(b => !desativado(b));
  if (!alvo) return null;
  alvo.click();
  let confirmou = false;
  const sumiu = () => !alvo.isConnected || !visivel(alvo) || !reAlvo.test((alvo.textContent || "").trim());
  // espera a janelinha de confirmação OU o botão sumir (quando não há confirmação)
  const conf = await esperar(() => (sumiu() ? "sumiu" : acharConfirmar(alvo, reAlvo)), 4000);
  if (conf && conf !== "sumiu") { clicavel(conf).click(); confirmou = true; }
  const saiu = await esperar(() => (sumiu() ? true : null), 8000, () => {
    if (!confirmou) { const c = acharConfirmar(alvo, reAlvo); if (c) { clicavel(c).click(); confirmou = true; } }
  });
  const segundos = Math.round((performance.now() - t0) / 100) / 10;
  if (saiu) return { ok: true, segundos, detalhe: `${nome} no Gerenciador de LIVE.` };
  return { ok: false, segundos, detalhe: `Cliquei em '${(alvo.textContent || "").trim()}', mas o TikTok não confirmou. Confira a aba do TikTok.` };
}

function diagnostico() {
  const vistos = new Set(), itens = [];
  for (const el of document.querySelectorAll("button,[role=button],a,[class*=btn],[class*=Btn],[class*=button],[class*=Button]")) {
    if (!visivel(el)) continue;
    const t = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!t || t.length > 40 || vistos.has(t)) continue;
    vistos.add(t);
    itens.push(t);
    if (itens.length >= 60) break;
  }
  return itens;
}

// Só responde quem tem o botão; assim, numa página com quadros, o quadro certo é quem responde.
chrome.runtime.onMessage.addListener((msg, _remetente, responder) => {
  const acoes = {
    tiktok_pausar: [RE_PAUSAR, "Live pausada"],
    tiktok_retomar: [RE_RETOMAR, "Live retomada"],
    tiktok_encerrar: [RE_ENCERRAR, "Live encerrada"],
  };
  if (msg.tipo === "tiktok_encerrar" && iconeEncerrar()) {
    encerrarPeloIcone().then(r => responder(r || { ok: false, detalhe: "Ícone ⏻ não encontrado." }),
                             (e) => responder({ ok: false, detalhe: String(e) }));
    return true;   // resposta assíncrona
  }
  if (acoes[msg.tipo]) {
    const [re, nome] = acoes[msg.tipo];
    if (!achar(re).length) {
      // sem o botão: só o quadro principal responde, explicando (e só se não houver quadros
      // embutidos, que podem ter o botão e responder depois)
      if (window.top !== window || document.querySelector("iframe")) return false;
      const det = msg.tipo === "tiktok_pausar"
        ? "O Gerenciador de LIVE não mostra um botão de pausar agora."
        : msg.tipo === "tiktok_retomar" ? "O Gerenciador de LIVE não mostra um botão de retomar agora."
        : "Não encontrei o botão de encerrar a live no Gerenciador de LIVE.";
      responder({ ok: false, detalhe: det, botoes: diagnostico() });
      return false;
    }
    clicarEConfirmar(re, nome).then(r => responder(r || { ok: false, detalhe: "Botão não encontrado." }),
                                    (e) => responder({ ok: false, detalhe: String(e) }));
    return true;   // resposta assíncrona
  }
  if (msg.tipo === "tiktok_diagnostico") {
    if (window.top !== window) return false;
    const c = contadorVisivel();
    responder({ ok: true, situacao: situacao(), tem_pausa: achar(RE_PAUSAR).length > 0, botoes: diagnostico(),
                encerrar: !!iconeEncerrar(), contador: c ? c.textContent.trim() : "",
                detalhe: "Botões lidos." });
  }
  return false;
});

}
