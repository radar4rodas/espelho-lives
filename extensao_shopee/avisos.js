// Vigia de avisos de restrição: roda na Shopee Live e no Gerenciador de LIVE do TikTok Shop.
// Lê, na hora, as janelinhas, faixas e notificações da própria plataforma (não o chat) e, se uma
// delas disser que a live/conta foi restringida, suspensa, encerrada por violação etc., avisa o
// painel, que encerra a live só na plataforma onde o aviso apareceu.
//
// Para não encerrar à toa:
// - só olha elementos com cara de aviso (janela, faixa, notificação), nunca a área de comentários;
// - só conta frases que dizem que algo JÁ aconteceu ("sua live foi restringida", "detectamos uma
//   violação"); lembretes ("lives que violam as regras podem ser encerradas"), negações ("sua conta
//   não está restrita"), perguntas de ajuda e quedas de internet ("transmissão interrompida") não contam.

if (!globalThis.__espelhoAvisosCarregado) {
globalThis.__espelhoAvisosCarregado = true;

const SITE = location.hostname.includes("tiktok") ? "tiktok" : "shopee";

// elementos com cara de aviso
const SEL_AVISO = [
  "[role=alert]", "[role=alertdialog]", "[role=dialog]", "[role=status]", "[aria-live=assertive]",
  "[class*=modal]", "[class*=Modal]", "[class*=dialog]", "[class*=Dialog]", "[class*=toast]", "[class*=Toast]",
  "[class*=notice]", "[class*=Notice]", "[class*=notif]", "[class*=Notif]", "[class*=banner]", "[class*=Banner]",
  "[class*=alert]", "[class*=Alert]", "[class*=warn]", "[class*=Warn]", "[class*=popup]", "[class*=Popup]",
  "[class*=violat]", "[class*=Violat]", "[class*=punish]", "[class*=Punish]", "[class*=penalt]", "[class*=Penalt]",
  "[class*=restrict]", "[class*=Restrict]", "[class*=drawer]", "[class*=Drawer]",
  ".arco-message", ".arco-notification", ".arco-alert", ".arco-modal"
].join(",");
// comentários e chat dos espectadores: nunca contam (alguém poderia escrever a frase de propósito)
const RE_CHAT = /comment|chat|danmu|barrage|bullet|im-?msg|im_msg|message-?list|msg-?list|msglist/i;
const MAX_TEXTO = 1500;

const semAcento = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "");
const normalizar = (s) => semAcento(String(s || "").toLowerCase()).replace(/\s+/g, " ").trim();

// (?:\S+\s+){0,4} = até 4 palavras no meio
const PADROES = [
  // "sua live foi restringida", "a conta está suspensa", "sua loja foi bloqueada"
  // (sem "interrompida", que também aparece em queda de internet, e sem "limitada a 4 horas")
  /\b(live|lives|transmissao|transmissoes|conta|loja|perfil|stream)\s+(?:\S+\s+){0,4}(foi|foram|esta|estao|teve|tiveram|ficou|ficara)\s+(?:\S+\s+){0,2}(restrit|restringid|suspens|suspend|banid|bloquead|penalizad)/,
  // "alcance limitado/restrito"
  /\balcance\s+(?:\S+\s+){0,4}(limitad|restrit|reduzid)/,
  // "encerrada por violar as diretrizes", "interrompida devido a violação das políticas"
  /\b(encerrad|interrompid|removid|finalizad|cortad|derrubad)\w*\s+(?:\S+\s+){0,4}(por|devido|em razao|pois|porque)\s+(?:\S+\s+){0,4}(violac|violar|violou|viola\b|violando|infrac|infring|descumpr|proibid)/,
  // "você violou", "você recebeu uma advertência"
  /\b(voce|vc)\s+(?:\S+\s+){0,2}(violou|infringiu|descumpriu|recebeu\s+(uma\s+|um\s+)?(advertencia|penalidade|strike|punicao|infracao|aviso de violacao|notificacao de violacao))/,
  // "detectamos conteúdo proibido", "identificamos uma violação"
  /\b(detectamos|identificamos|constatamos|verificamos)\s+(?:\S+\s+){0,6}(violac|viola\b|violando|proibid|infrac|nao permitid|improprio|inadequad)/,
  // "advertência aplicada", "violação confirmada"
  /\b(advertencia|penalidade|strike|punicao|infracao|violacao)\s+(?:\S+\s+){0,4}(aplicad|registrad|emitid|confirmad|detectad|recebid)/,
  // inglês
  /\b(your|this)\s+(live|stream|account|shop|broadcast)\s+(?:\S+\s+){0,3}(has been|have been|was|is|is being|got)\s+(?:\S+\s+){0,2}(restricted|suspended|banned|blocked|penalized)/,
  /\breach\s+(?:\S+\s+){0,3}(limited|restricted|reduced)/,
  /\b(ended|interrupted|removed|terminated|stopped)\s+(?:\S+\s+){0,4}(due to|for|because of)\s+(?:\S+\s+){0,4}(violat|prohibited)/,
  /\bwe(?:'ve| have)?\s+(detected|found|identified)\s+(?:\S+\s+){0,5}(violat|prohibited|not allowed|inappropriate)/,
  /\byou\s+(?:\S+\s+){0,2}(violated|received\s+(a\s+)?(warning|strike|penalty|violation))/,
  // espanhol
  /\b(tu|su|el|la)\s+(live|transmision|directo|cuenta|tienda)\s+(?:\S+\s+){0,3}(ha sido|fue|esta)\s+(restringid|suspendid|bloquead|baneado)/,
];
const RE_NEGA = /\b(nao|not|nunca|never|sem|isn't|wasn't)\b/;

function ehRestricao(texto) {
  for (const re of PADROES) {
    const m = re.exec(texto);
    if (!m) continue;
    if (RE_NEGA.test(m[0].replace(/nao permitid\w*|not allowed/g, ""))) continue;   // "sua conta não está restrita"
    const resto = texto.slice(m.index + m[0].length);
    const fim = resto.search(/[.!?\n]/);
    if (fim >= 0 && resto[fim] === "?") continue;           // pergunta de ajuda: "por que minha live foi restringida?"
    return true;
  }
  return false;
}

function visivel(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) > 0.05;
}
const classe = (el) => (el.getAttribute && el.getAttribute("class")) || "";
const ehIcone = (el) => el instanceof SVGElement;

// está dentro (ou é) da área de comentários?
function noChat(el) {
  let a = el;
  for (let i = 0; a && a !== document.body && i < 10; i++, a = a.parentElement) {
    if (RE_CHAT.test(classe(a)) || RE_CHAT.test(a.id || "")) return true;
  }
  return false;
}
// tem a área de comentários dentro? (aí é um pedaço grande da página, não um aviso)
function temChatDentro(el) {
  for (const d of el.querySelectorAll("[class],[id]")) {
    if (ehIcone(d)) continue;
    if (RE_CHAT.test(classe(d)) || RE_CHAT.test(d.id || "")) return true;
  }
  return false;
}

const jaAvisados = new Set();
function varrer() {
  const achados = [];
  for (const el of document.querySelectorAll(SEL_AVISO)) {
    if (el === document.body || el === document.documentElement || ehIcone(el)) continue;
    const bruto = (el.innerText || "").trim();
    if (bruto.length < 12 || bruto.length > MAX_TEXTO) continue;
    const norm = normalizar(bruto);
    if (jaAvisados.has(norm) || !ehRestricao(norm)) continue;
    if (!visivel(el) || noChat(el) || temChatDentro(el)) continue;
    achados.push({ norm, texto: bruto.replace(/\s+/g, " ").slice(0, 400) });
  }
  // fica com o aviso mais curto (o mais "interno") quando um está dentro do outro
  achados.sort((a, b) => a.norm.length - b.norm.length);
  for (const a of achados) {
    if ([...jaAvisados].some(j => a.norm.includes(j))) { jaAvisados.add(a.norm); continue; }
    jaAvisados.add(a.norm);
    try { chrome.runtime.sendMessage({ tipo: "aviso", site: SITE, texto: a.texto }).catch(() => {}); } catch (_) {}
  }
}

// para o teste e o diagnóstico
globalThis.__espelhoAvisos = { ehRestricao: (t) => ehRestricao(normalizar(t)), varrer };

let espera = null;
new MutationObserver(() => {
  if (espera) return;
  espera = setTimeout(() => { espera = null; try { varrer(); } catch (_) {} }, 250);
}).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
setInterval(() => { try { varrer(); } catch (_) {} }, 3000);
try { varrer(); } catch (_) {}

}
