// Comentários da live: roda na página da sua live da Shopee e no Gerenciador de LIVE do TikTok Shop.
// - Lê os últimos comentários do chat da própria página (nome + texto) e manda para o painel,
//   que mostra no celular, separado por plataforma.
// - Quando você responde pelo celular, escreve o texto na caixa de comentário da página
//   ("Diga algo..." na Shopee, "Digite algo..." no TikTok) e envia.
// Só lê e escreve no chat; não clica em mais nada.

if (!globalThis.__espelhoComentariosCarregado) {
globalThis.__espelhoComentariosCarregado = true;

// (__espelhoSiteTeste: só para o teste com páginas falsas; o site real nunca enxerga esta variável)
const SITE = globalThis.__espelhoSiteTeste || (location.hostname.includes("tiktok") ? "tiktok" : "shopee");
const CFG = {
  tiktok: { caixa: /digite algo|type something|say something|adicione um coment|add a comment/i, max: 100,
            ignorar: /acab(ou|aram) de entrar|just joined|\bentr(ou|aram)\b|seguiu|compartilhou|curtiu|est[áa] comprando/i },
  shopee: { caixa: /diga algo|say something|digite|comente/i, max: 150,
            ignorar: /\bentr(ou|aram)\b|\bjoined\b|est[áa] comprando|comprou|seguiu|compartilhou|curtiu/i },
}[SITE];
const MAX_COMENTARIOS = 30;

function visivel(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
}
const desativado = (el) => el.disabled || el.getAttribute("aria-disabled") === "true" ||
                           /disabled/i.test(typeof el.className === "string" ? el.className : "");

// ---- a caixa de comentário (onde se escreve)
let caixaGuardada = null;
function caixa() {
  if (caixaGuardada && caixaGuardada.isConnected && visivel(caixaGuardada)) return caixaGuardada;
  caixaGuardada = null;
  for (const el of document.querySelectorAll("textarea,input,[contenteditable='true'],[contenteditable='']")) {
    if (el.tagName === "INPUT" && !/^(text|search|)$/i.test(el.type || "")) continue;
    const dica = [el.getAttribute("placeholder"), el.getAttribute("data-placeholder"), el.getAttribute("aria-label")]
      .filter(Boolean).join(" ");
    if (CFG.caixa.test(dica) && visivel(el)) { caixaGuardada = el; break; }
  }
  return caixaGuardada;
}
const valorDa = (el) => el.isContentEditable ? (el.textContent || "") : (el.value || "");

// ---- a lista de comentários: a área com rolagem mais perto da caixa de comentário
function rola(el) {
  const oy = getComputedStyle(el).overflowY;
  return (oy === "auto" || oy === "scroll" || oy === "overlay") && el.clientHeight > 60;
}
// Só vale a área que fica ACIMA da caixa e na MESMA COLUNA (no Gerenciador do TikTok o quadro
// "Atividade" ao lado também rola e é maior: era ele que estava sendo lido no lugar do chat).
function mesmaColuna(el, cx) {
  const r = el.getBoundingClientRect(), c = cx.getBoundingClientRect();
  const sobra = Math.min(r.right, c.right) - Math.max(r.left, c.left);   // largura em comum
  return sobra > Math.min(r.width, c.width) * 0.6 && r.top < c.top;
}
function listaDeComentarios() {
  const cx = caixa();
  if (!cx) return null;
  let a = cx.parentElement;
  for (let nivel = 0; a && a !== document.body && nivel < 8; nivel++, a = a.parentElement) {
    const achadas = [...a.querySelectorAll("div,ul,section")]
      .filter(el => !el.contains(cx) && rola(el) && visivel(el) && mesmaColuna(el, cx));
    if (achadas.length) return achadas.sort((x, y) => y.scrollHeight - x.scrollHeight)[0];
  }
  return null;
}
function linhas(lista) {
  let el = lista;
  for (let i = 0; i < 6; i++) {
    const filhos = [...el.children].filter(c => (c.innerText || "").trim());
    // um filho só: é o "miolo" da lista (desce nele)... a não ser que seja baixinho, aí é o
    // único comentário que existe (não desce, senão nome e texto viram duas linhas)
    if (filhos.length === 1 && filhos[0].getBoundingClientRect().height >= 90) el = filhos[0]; else break;
  }
  return [...el.children];
}
// os pedaços de texto da linha, separados pelo elemento onde estão (a Shopee põe selo, nome e texto
// lado a lado, sem quebra de linha, e o innerText sai colado: "ApresentadorLive Ofertaskkkk")
function pedacos(linha) {
  const out = [];
  let pai = null;
  const w = document.createTreeWalker(linha, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const t = n.nodeValue.replace(/\s+/g, " ").trim();
    const el = n.parentElement;
    if (!t || !el || !visivel(el)) continue;
    if (el === pai) out[out.length - 1] += " " + t; else { out.push(t); pai = el; }
  }
  return out;
}
const RE_SELO = /^(apresentador[a]?|anfitri[ãa]o?|host|moderador[a]?)$/i;     // selo antes do SEU nome
const RE_EVENTO = /\bentr(ou|aram)\s+(na\s+live\s+)?por\b|\boutr[oa]s?\s+(\d+\s+)?usu[aá]rios?\b|\b\d+\s+outr[oa]s?\s+usu[aá]rios?\b|come[çc](ou|ar|aram) a seguir|seguir o apresentador|est(á|a|ão|ao) comprando|est(á|a|ão|ao) vendo o produto|acab(ou|aram) de entrar|bem-vind[oa] (à|a|ao) shopee live/i;
const RE_SISTEMA_FRASE = /acab(ou|aram) de entrar|est(á|a|ão|ao) vendo o produto/i;
const RE_SISTEMA_MEIO = /bem-vind[oa] (à|a|ao) shopee live|\w\.\.\.\s+(e\s+(mais\s+)?\d+\s+)?(entr(ou|aram)|est(á|a|ão|ao) comprando|come[çc](ou|aram)|acab(ou|aram)|compr(ou|aram)|segui(u|ram)|compartilh(ou|aram)|curti(u|ram))/i;   // avisos colados numa linha só
const RE_INICIO = /^(acab(ou|aram) de entrar|entr(ou|aram)|est(á|a|ão|ao) comprando|compr(ou|aram)|segui(u|ram)|acab(ou|aram) de come[çc]ar a seguir|come[çc](ou|aram) a seguir|compartilh(ou|aram)|curti(u|ram)|just joined|joined)\b/i;
function lerLinha(linha) {
  let partes = (linha.innerText || "").split("\n").map(s => s.trim()).filter(Boolean);
  const porElemento = pedacos(linha);
  if (porElemento.length > partes.length) partes = porElemento;
  let eu = false;
  partes = partes.filter(p => { if (RE_SELO.test(p)) { eu = true; return false; } return true; });
  while (partes.length > 1 && (/^\d+$/.test(partes[0]) || partes[0].length <= 1)) partes.shift();   // nível, selo
  if (!partes.length) return null;
  const tudo = partes.join(" ");
  // "fulano está comprando produtos!" / "fulano e 3 estão comprando produtos!": a seu pedido, fora do chat
  if (!eu && /est(á|a|ão|ao) comprando/i.test(tudo) && tudo.length < 200) return null;
  // avisos automáticos ("entrou na live por Nº...", "e outros 3 usuários entraram por", "começou a seguir",
  // "bem-vindo à Shopee Live"...): a pedido (24/11) o celular mostra só clientes e as suas respostas
  if (!eu && (RE_EVENTO.test(tudo) || RE_SISTEMA_MEIO.test(tudo))) return null;
  if (!eu && tudo.length < 160 && (RE_SISTEMA_FRASE.test(tudo) ||
      (partes.length === 1 ? CFG.ignorar.test(tudo)
        : RE_INICIO.test(partes.slice(1).join(" ").replace(/^(e|and)\s+(mais\s+)?\d+\s+(outr[oa]s\s+|pessoas\s+|others?\s+)?/i, ""))))) return null;
  if (partes.length === 1) {
    const m = partes[0].match(/^([^:]{1,40}):\s*(.+)$/);                   // "nome: texto" numa linha só
    return m ? { nome: m[1].trim(), texto: m[2].trim(), eu } : { nome: "", texto: partes[0], eu };
  }
  return { nome: partes[0].slice(0, 60), texto: partes.slice(1).join(" ").slice(0, 300), eu };
}
function ler() {
  const lista = listaDeComentarios();
  if (!lista) return null;
  const itens = [];
  for (const l of linhas(lista)) {
    const c = lerLinha(l);
    if (c && c.texto) itens.push(c);
  }
  return itens.slice(-MAX_COMENTARIOS);
}

// ---- manda para a extensão quando muda (e a cada 10 s, para o painel saber que a página continua aberta)
let ultimo = "";
function reportar(forcar) {
  let itens = null;
  try { itens = ler(); } catch (_) {}
  if (itens === null && !caixa()) return;          // este quadro não tem chat
  const chave = JSON.stringify(itens);
  if (!forcar && chave === ultimo) return;
  ultimo = chave;
  try { chrome.runtime.sendMessage({ tipo: "comentarios", site: SITE, lista: itens || [], caixa: !!caixa() }).catch(() => {}); } catch (_) {}
}
let espera = null;
new MutationObserver(() => {
  if (espera) return;
  espera = setTimeout(() => { espera = null; reportar(false); }, 600);
}).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
setInterval(() => reportar(true), 10000);
reportar(true);

// ---- responder pelo celular
function esperar(cond, ms) {
  return new Promise((resolve) => {
    if (cond()) return resolve(true);
    let fim = false;
    const obs = new MutationObserver(() => { if (!fim && cond()) { fim = true; obs.disconnect(); clearTimeout(t); resolve(true); } });
    obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
    const t = setTimeout(() => { fim = true; obs.disconnect(); resolve(!!cond()); }, ms);
  });
}
function escrever(el, texto) {
  el.focus();
  if (el.isContentEditable) {
    document.execCommand("selectAll", false, null);
    if (!document.execCommand("insertText", false, texto)) {
      el.textContent = texto;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: texto, inputType: "insertText" }));
    }
    return;
  }
  // campos do React: precisa usar o "setter" original para a página perceber o texto
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, texto);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}
function botaoEnviar(cx) {
  let a = cx.parentElement;
  for (let nivel = 0; a && a !== document.body && nivel < 5; nivel++, a = a.parentElement) {
    for (const b of a.querySelectorAll("button,[role=button]")) {
      if (/^\s*(enviar|send|postar|publicar)\s*$/i.test(b.textContent || "") && visivel(b)) return b;
    }
    // TikTok: ícone de enviar ao lado da caixa (junto do contador "0/100")
    if (/\d+\s*\/\s*\d+/.test(a.innerText || "")) {
      const icones = [...a.querySelectorAll("svg")].filter(s => !cx.contains(s) && visivel(s));
      if (icones.length) {
        const s = icones[icones.length - 1];
        return s.closest("button,[role=button],[class*=send],[class*=Send],[class*=cursor-pointer]") || s.parentElement;
      }
    }
  }
  return null;
}
function apertarEnter(el) {
  for (const tipo of ["keydown", "keypress", "keyup"]) {
    el.dispatchEvent(new KeyboardEvent(tipo, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  }
}
async function responder(texto) {
  const cx = caixa();
  if (!cx) return { ok: false, detalhe: "Não achei a caixa de comentário na página da live." };
  texto = String(texto || "").replace(/\s+/g, " ").trim().slice(0, CFG.max);
  if (!texto) return { ok: false, detalhe: "Escreva a resposta primeiro." };
  escrever(cx, texto);
  await new Promise(r => setTimeout(r, 200));
  if (valorDa(cx).trim() !== texto) return { ok: false, detalhe: "Não consegui escrever na caixa de comentário. Responda pela aba do Chrome." };
  const b = botaoEnviar(cx);
  if (b && !desativado(b)) b.click(); else apertarEnter(cx);
  let enviou = await esperar(() => valorDa(cx).trim() === "", 2500);
  if (!enviou && b) { apertarEnter(cx); enviou = await esperar(() => valorDa(cx).trim() === "", 2000); }
  setTimeout(() => reportar(true), 800);
  return enviou ? { ok: true, detalhe: "Resposta enviada no chat " + (SITE === "tiktok" ? "do TikTok." : "da Shopee.") }
                : { ok: false, detalhe: "Escrevi a resposta, mas a página não enviou. Confira a aba do Chrome." };
}

// só o quadro que tem a caixa de comentário responde
chrome.runtime.onMessage.addListener((msg, _remetente, responderMsg) => {
  if (msg.tipo !== SITE + "_responder" || !caixa()) return false;
  responder(msg.texto).then(responderMsg, (e) => responderMsg({ ok: false, detalhe: String(e) }));
  return true;   // resposta assíncrona
});

// para o teste e o diagnóstico
globalThis.__espelhoComentarios = { ler, responder, caixa, listaDeComentarios };

}
