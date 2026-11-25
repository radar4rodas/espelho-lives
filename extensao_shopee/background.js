// Ponte entre o painel (ws://127.0.0.1:8766, no seu computador) e as abas da Shopee Live
// e do Gerenciador de LIVE do TikTok Shop.
// A conexão aberta mantém a extensão ativa enquanto o painel estiver rodando.

const PAINEL = "ws://127.0.0.1:8766";
const SONDA = "http://127.0.0.1:8765/";        // o painel está no ar?
const PADROES = ["https://live.shopee.com.br/*", "https://shop.tiktok.com/*"];
let ws = null;
let tentarDeNovo = null;
// id da aba -> { site, url, quadros: { idQuadro: {situacao, tem_chave, tem_pausa, numeros} }, titulo, quando }
// (a url da aba da Shopee diz qual live está aberta: ...?session=123)
const abas = {};

const siteDaUrl = (url) => (url || "").includes("shop.tiktok.com") ? "tiktok" : "shopee";
const ARQUIVO = { shopee: "content.js", tiktok: "content_tiktok.js" };
const COMUNS = ["comentarios.js", "avisos.js"];   // rodam nos dois sites, depois do arquivo do site
const avisosPendentes = [];   // avisos de restrição que chegaram com o painel desconectado

function resumoAbas() {
  const r = {};
  for (const [id, a] of Object.entries(abas)) {
    const qs = Object.values(a.quadros);
    const situacao = qs.some(q => q.situacao === "ao_vivo") ? "ao_vivo"
                   : qs.some(q => q.situacao === "preparada") ? "preparada" : "outra";
    const comNumeros = qs.find(q => q.numeros && q.numeros.espectadores != null) || qs.find(q => q.numeros);
    r[id] = { site: a.site, situacao, tem_chave: qs.some(q => q.tem_chave), tem_pausa: qs.some(q => q.tem_pausa),
              titulo: a.titulo, quando: a.quando, url: a.url || "", numeros: comNumeros ? comNumeros.numeros : null,
              comentarios: a.comentarios || null, tem_caixa: !!a.tem_caixa };
  }
  return r;
}
function enviar(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
  return false;
}
// aviso de restrição: vai na hora; se o painel estiver desconectado, vai assim que conectar (até 2 min)
function enviarAviso(av) {
  if (enviar({ tipo: "aviso", ...av })) return;
  avisosPendentes.push({ ...av, quando: Date.now() });
  conectar();
}
function enviarAvisosPendentes() {
  while (avisosPendentes.length) {
    const av = avisosPendentes.shift();
    if (Date.now() - av.quando < 120000) enviar({ tipo: "aviso", site: av.site, texto: av.texto });
  }
}
function enviarEstado() { enviar({ tipo: "estado", abas: resumoAbas() }); }

function registrar(idAba, idQuadro, e, url) {
  const a = abas[idAba] || (abas[idAba] = { site: e.site || siteDaUrl(url), quadros: {}, titulo: "", quando: 0 });
  a.site = e.site || a.site;
  if (url) a.url = url;
  a.quadros[idQuadro] = { situacao: e.situacao, tem_chave: !!e.tem_chave, tem_pausa: !!e.tem_pausa,
                          numeros: e.numeros || null };
  if (e.principal) a.titulo = e.titulo;
  a.quando = Date.now();
}

// Só tenta abrir a conexão se o painel estiver no ar (evita encher a lista de erros da extensão).
async function conectar() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  try { await fetch(SONDA, { mode: "no-cors", cache: "no-store" }); } catch (_) { agendar(5000); return; }
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  try { ws = new WebSocket(PAINEL); } catch (_) { agendar(5000); return; }
  ws.onopen = () => { enviarAvisosPendentes(); atualizarAbas(); };
  ws.onclose = () => { ws = null; agendar(2000); };
  ws.onerror = () => {};
  ws.onmessage = (ev) => { try { tratar(JSON.parse(ev.data)); } catch (_) {} };
}
function agendar(ms) {
  if (tentarDeNovo) return;
  tentarDeNovo = setTimeout(() => { tentarDeNovo = null; conectar(); }, ms);
}

// Consulta todas as abas da Shopee Live e do TikTok Shop; onde a extensão ainda não está ativa
// (aba aberta antes de instalar/atualizar), ativa na hora, sem precisar apertar F5.
async function atualizarAbas() {
  const lista = await chrome.tabs.query({ url: PADROES });
  for (const id of Object.keys(abas)) if (!lista.some(t => String(t.id) === id)) delete abas[id];
  await Promise.all(lista.map(async (t) => {
    // a "Economia de memória" do Chrome descarta abas paradas há muito tempo; aí a extensão
    // some da página e não consegue finalizar a live no fim do loop. Estas abas nunca são descartadas.
    if (t.autoDiscardable !== false) chrome.tabs.update(t.id, { autoDiscardable: false }).catch(() => {});
    if (t.discarded) return;
    let res = [];
    try {
      res = await chrome.scripting.executeScript({
        target: { tabId: t.id, allFrames: true },
        func: () => (globalThis.__espelhoEstado && globalThis.__espelhoAvisosCarregado && globalThis.__espelhoComentariosCarregado
                     ? globalThis.__espelhoEstado() : null)
      });
    } catch (_) { return; }
    if (res.some(r => r.result === null)) {
      try {
        await chrome.scripting.executeScript({ target: { tabId: t.id, allFrames: true }, files: [ARQUIVO[siteDaUrl(t.url)], ...COMUNS] });
      } catch (_) {}
      return;   // os quadros recém-ativados se reportam sozinhos
    }
    for (const r of res) if (r.result) registrar(t.id, r.frameId, r.result, t.url);
  }));
  enviarEstado();
}

const PRECISA_AO_VIVO = new Set(["finalizar", "tiktok_pausar", "tiktok_encerrar", "tiktok_responder", "shopee_responder"]);
const RESPONDER = new Set(["tiktok_responder", "shopee_responder"]);
const SEM_RESPOSTA = {
  finalizar: "Não encontrei nenhuma aba da Shopee Live com o botão 'Finalizar Transmissão'.",
  pegar_chave: "Não achei a URL e a chave. Abra a tela 'Associar transmissão com OBS' da sua live.",
  tiktok_pausar: "Não encontrei o Gerenciador de LIVE do TikTok Shop com a live no ar.",
  tiktok_retomar: "Não encontrei o Gerenciador de LIVE do TikTok Shop aberto.",
  tiktok_encerrar: "Não encontrei o Gerenciador de LIVE do TikTok Shop com a live no ar.",
  tiktok_diagnostico: "Não encontrei o Gerenciador de LIVE do TikTok Shop aberto no Chrome.",
  tiktok_responder: "Não achei a caixa de comentário no Gerenciador de LIVE do TikTok.",
  shopee_responder: "Não achei a caixa de comentário na página da sua live da Shopee.",
};

function escolherAba(tipo) {
  const site = tipo.startsWith("tiktok_") ? "tiktok" : "shopee";
  const resumo = resumoAbas();
  const doSite = Object.keys(resumo).filter(id => resumo[id].site === site);
  let ids = doSite.filter(id =>
    RESPONDER.has(tipo) ? resumo[id].tem_caixa
    : PRECISA_AO_VIVO.has(tipo) ? resumo[id].situacao === "ao_vivo"
    : tipo === "pegar_chave" ? resumo[id].tem_chave : true);
  if (!ids.length && (tipo === "pegar_chave" || tipo === "tiktok_pausar" || tipo === "tiktok_encerrar" || RESPONDER.has(tipo))) ids = doSite;
  ids.sort((a, b) => resumo[b].quando - resumo[a].quando);
  return ids.length ? Number(ids[0]) : null;
}

async function tratar(m) {
  if (m.tipo === "ping") { enviar({ tipo: "pong" }); return; }
  if (!(m.tipo in SEM_RESPOSTA)) return;
  const semResposta = SEM_RESPOSTA[m.tipo];
  let alvo = escolherAba(m.tipo);
  if (alvo === null) { await atualizarAbas(); alvo = escolherAba(m.tipo); }
  if (alvo === null) {
    enviar({ tipo: "resultado", id: m.id, ok: false, sem_aba: true, detalhe: semResposta });
    return;
  }
  try {
    // vai para todos os quadros da aba; só responde o quadro que tem o botão / os campos
    const pedido = RESPONDER.has(m.tipo) ? { tipo: m.tipo, texto: String(m.texto || "").slice(0, 200) } : { tipo: m.tipo };
    const r = await chrome.tabs.sendMessage(alvo, pedido);
    enviar({ tipo: "resultado", id: m.id, ...(r || { ok: false, detalhe: semResposta }) });
  } catch (e) {
    enviar({ tipo: "resultado", id: m.id, ok: false, sem_aba: true, detalhe: semResposta });
  }
}

chrome.runtime.onMessage.addListener((msg, remetente) => {
  if (msg.tipo === "aviso" && remetente.tab) {
    enviarAviso({ site: msg.site || siteDaUrl(remetente.tab.url), texto: String(msg.texto || "").slice(0, 400) });
    return;
  }
  if (msg.tipo === "comentarios" && remetente.tab) {
    const id = remetente.tab.id;
    const a = abas[id] || (abas[id] = { site: msg.site || siteDaUrl(remetente.tab.url), quadros: {}, titulo: "", quando: 0 });
    if (remetente.tab.url) a.url = remetente.tab.url;
    a.comentarios = Array.isArray(msg.lista) ? msg.lista.slice(-30) : [];
    a.tem_caixa = !!msg.caixa;
    conectar();
    enviarEstado();
    return;
  }
  if (msg.tipo === "estado" && remetente.tab) {
    registrar(remetente.tab.id, remetente.frameId || 0, msg, remetente.tab.url);
    conectar();
    enviarEstado();
  }
});
chrome.tabs.onRemoved.addListener((id) => { if (abas[id]) { delete abas[id]; enviarEstado(); } });
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === "loading" && abas[id]) { abas[id].quadros = {}; }
  if (info.url && abas[id]) { abas[id].url = info.url; }
});
chrome.runtime.onStartup.addListener(conectar);
chrome.runtime.onInstalled.addListener(() => { conectar(); atualizarAbas(); });
conectar();
