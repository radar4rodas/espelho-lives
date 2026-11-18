// Roda dentro das páginas da Shopee Live (inclusive em quadros embutidos da mesma página).
// - Conta para a extensão em que situação a página está (live no ar, pronta, outra).
// - Quando o painel manda, clica em "Finalizar Transmissão" e confirma.
// - Lê a URL e a chave da tela "Associar transmissão com OBS".

if (!globalThis.__espelhoLiveCarregado) {
globalThis.__espelhoLiveCarregado = true;

const RE_FIN = /finalizar\s+transmiss/i;
const RE_ENTRAR = /entrar\s+ao\s+vivo/i;
const RE_CONF = /^\s*(confirmar|finalizar|encerrar|ok|sim|terminar)\b/i;
const RE_CANC = /cancel|voltar|não|nao/i;
const RE_URL = /^rtmps?:\/\/\S+$/i;
const RE_ROTULO = /^(copy|copiar|copiado|chave|url|server|stream key)$/i;
const SEL_DIALOGO = "[role=dialog],[role=alertdialog],[class*=modal],[class*=Modal],[class*=dialog]," +
                    "[class*=Dialog],[class*=popup],[class*=confirm],[class*=popover]";

function visivel(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
}

// elementos visíveis cujo texto bate, ficando só com os mais "internos"
function achar(re, seletor = "button,[role=button],a,span") {
  const els = [...document.querySelectorAll(seletor)].filter(e => re.test(e.textContent || "") && visivel(e));
  return els.filter(e => ![...e.children].some(c => re.test(c.textContent || "")));
}
const clicavel = (el) => el.closest("button,[role=button],a") || el;

function situacao() {
  if (achar(RE_FIN).length) return "ao_vivo";
  if (achar(RE_ENTRAR).length) return "preparada";
  return "outra";
}

// junta, em ordem, os valores de campos e os textos "folha" da página (inclui componentes com shadow DOM)
function coletarTextos(raiz, acc) {
  for (const el of raiz.querySelectorAll("*")) {
    if (el.shadowRoot) coletarTextos(el.shadowRoot, acc);
    const tag = el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") {
      const v = (el.value || el.getAttribute("value") || "").trim();
      if (v) acc.push(v);
    } else if (tag !== "SCRIPT" && tag !== "STYLE" && el.children.length === 0) {
      const t = (el.textContent || "").trim();
      if (t && t.length < 500) acc.push(t);
    }
  }
  return acc;
}

function chaves() {
  const vals = coletarTextos(document, []);
  const i = vals.findIndex(v => RE_URL.test(v));
  if (i < 0) return null;
  const chave =
    vals.find(v => /^br-live-\S+/i.test(v)) ||
    vals.find(v => /[?&]\w*secret=/i.test(v) && !RE_URL.test(v)) ||
    vals.slice(i + 1).find(v => v.length > 10 && !/\s/.test(v) && !RE_URL.test(v) && !RE_ROTULO.test(v)) ||
    "";
  return { url: vals[i], chave };
}

function estado() {
  return { tipo: "estado", situacao: situacao(), tem_chave: !!chaves(), titulo: document.title,
           principal: window.top === window };
}
globalThis.__espelhoEstado = estado;   // a extensão consulta isto ao (re)conectar

let ultimo = "";
function reportar(forcar) {
  const e = estado();
  const k = e.situacao + "|" + e.tem_chave;
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

function acharConfirmar(original) {
  for (const d of document.querySelectorAll(SEL_DIALOGO)) {
    if (!visivel(d)) continue;
    for (const b of d.querySelectorAll("button,[role=button]")) {
      const t = (b.textContent || "").trim();
      if (b !== original && visivel(b) && RE_CONF.test(t) && !RE_CANC.test(t)) return b;
    }
  }
  // janelinha com o mesmo texto do botão: o último "Finalizar Transmissão" visível é o dela
  const outros = achar(RE_FIN).map(clicavel).filter(b => b !== original);
  return outros.length ? outros[outros.length - 1] : null;
}

async function finalizar() {
  const t0 = performance.now();
  const alvo = achar(RE_FIN, "button,[role=button],a,span,div")[0];
  if (!alvo) return null;
  const original = clicavel(alvo);
  original.click();
  let confirmou = false;
  const conf = await esperar(() => acharConfirmar(original), 4000);
  if (conf) { clicavel(conf).click(); confirmou = true; }
  const saiu = await esperar(() => (achar(RE_FIN).length === 0 ? true : null), 7000, () => {
    if (!confirmou) {
      const c = acharConfirmar(original);
      if (c) { clicavel(c).click(); confirmou = true; }
    }
  });
  const segundos = Math.round((performance.now() - t0) / 100) / 10;
  if (saiu) return { ok: true, segundos, detalhe: "Transmissão finalizada na Shopee." };
  return {
    ok: false, segundos,
    detalhe: "Cliquei em 'Finalizar Transmissão', mas a Shopee não confirmou o fim. Confira a aba da Shopee."
  };
}

// Só responde quem tem o que pedir; assim, numa página com quadros, o quadro certo é quem responde.
chrome.runtime.onMessage.addListener((msg, _remetente, responder) => {
  if (msg.tipo === "finalizar") {
    if (!achar(RE_FIN, "button,[role=button],a,span,div").length) return false;
    finalizar().then(responder, (e) => responder({ ok: false, detalhe: String(e) }));
    return true;   // resposta assíncrona
  }
  if (msg.tipo === "pegar_chave") {
    const c = chaves();
    if (!c) return false;
    responder({ ok: true, ...c });
  }
  if (msg.tipo === "relatar") {
    responder(estado());
  }
  return false;
});

}
