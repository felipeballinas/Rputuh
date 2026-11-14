// Gera os dados do comparador Dano / Builds a partir da TibiaWiki (tibia.fandom.com, conteúdo CC BY-SA 3.0, adaptado).
// Saída: data/builds/proficiencias.json e data/builds/equipamentos.json.
// O data/builds/wheel.json é mantido à mão: o layout vem do Wheel of Destiny Planner oficial (tibia.com).
// Uso: node scripts/gerar_dados_builds.mjs   (Node 18+; rode de vez em quando para atualizar os dados)
import { mkdir, writeFile, rename } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WIKI = 'https://tibia.fandom.com/api.php';
const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..');
const SAIDA = join(RAIZ, 'data', 'builds');
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

// Requisição à API do MediaWiki: tempo limite, nova tentativa só para 429/5xx/rede (respeitando Retry-After)
// e erro claro quando a API devolve { error } com HTTP 200.
async function wiki(params) {
  const url = `${WIKI}?${new URLSearchParams({ format: 'json', formatversion: '2', maxlag: '5', ...params })}`;
  for (let tentativa = 1; ; tentativa++) {
    let resp;
    try {
      resp = await fetch(url, { headers: { 'User-Agent': 'rputuh-gerador-dados/1.1 (+https://rputuh.com.br)' }, signal: AbortSignal.timeout(30_000) });
    } catch (erro) {
      if (tentativa >= 4) throw new Error(`Falha de rede na TibiaWiki (${erro.message}) em ${url}`);
      await espera(2000 * tentativa);
      continue;
    }
    if (resp.ok) {
      const json = await resp.json();
      if (json.error) {
        if (json.error.code === 'maxlag' && tentativa < 6) { await espera(5000); continue; }
        throw new Error(`TibiaWiki recusou a consulta (${json.error.code}: ${json.error.info}) em ${url}`);
      }
      return json;
    }
    const temporario = resp.status === 429 || resp.status >= 500;
    if (!temporario || tentativa >= 4) throw new Error(`TibiaWiki respondeu ${resp.status} em ${url}`);
    const retry = Number(resp.headers.get('retry-after'));
    await espera(Number.isFinite(retry) && retry > 0 ? retry * 1000 : 2000 * tentativa);
  }
}
const semComentarios = (t) => String(t || '').replace(/<!--[\s\S]*?-->/g, '');
async function textoDaPagina(pagina) {
  const r = await wiki({ action: 'parse', page: pagina, prop: 'wikitext' });
  if (!r.parse?.wikitext) throw new Error(`Página sem conteúdo: ${pagina}`);
  return semComentarios(r.parse.wikitext);
}
async function membrosDaCategoria(categoria) {
  const nomes = [];
  let cont;
  do {
    const r = await wiki({ action: 'query', list: 'categorymembers', cmtitle: `Category:${categoria}`, cmlimit: '500', cmnamespace: '0', ...(cont ? { cmcontinue: cont } : {}) });
    nomes.push(...(r.query?.categorymembers ?? []).map((m) => m.title));
    cont = r.continue?.cmcontinue;
  } while (cont);
  if (!nomes.length) throw new Error(`Categoria vazia ou inexistente: ${categoria}`);
  return nomes;
}
async function conteudos(titulos) {
  const mapa = new Map();
  for (let i = 0; i < titulos.length; i += 50) {
    const r = await wiki({ action: 'query', prop: 'revisions', rvprop: 'content', rvslots: 'main', titles: titulos.slice(i, i + 50).join('|') });
    for (const p of r.query?.pages ?? []) mapa.set(p.title, semComentarios(p.revisions?.[0]?.slots?.main?.content));
    await espera(300);
  }
  return mapa;
}
// Lê um campo da infobox. Para no fim da linha ou no próximo "|" de primeiro nível (fora de {{ }} e [[ ]]).
function campo(texto, chave) {
  const m = texto.match(new RegExp(`\\|\\s*${chave}\\s*=`));
  if (!m) return '';
  let i = m.index + m[0].length, nivel = 0, out = '';
  for (; i < texto.length; i++) {
    const dois = texto.slice(i, i + 2);
    if (dois === '{{' || dois === '[[') { nivel++; out += dois; i++; continue; }
    if ((dois === '}}' || dois === ']]') && nivel > 0) { nivel--; out += dois; i++; continue; }
    const c = texto[i];
    if (nivel === 0 && (c === '|' || c === '\n' || dois === '}}')) break;
    out += c;
  }
  return out.trim();
}
// Separa os parâmetros de um template respeitando {{ }} e [[ ]] aninhados.
function parametros(corpo) {
  const partes = [];
  let nivel = 0, atual = '';
  for (let i = 0; i < corpo.length; i++) {
    const dois = corpo.slice(i, i + 2);
    if (dois === '{{' || dois === '[[') { nivel++; atual += dois; i++; continue; }
    if (dois === '}}' || dois === ']]') { nivel--; atual += dois; i++; continue; }
    if (corpo[i] === '|' && nivel === 0) { partes.push(atual); atual = ''; continue; }
    atual += corpo[i];
  }
  partes.push(atual);
  return Object.fromEntries(partes.map((p) => { const k = p.indexOf('='); return k < 0 ? [p.trim(), ''] : [p.slice(0, k).trim(), p.slice(k + 1).trim()]; }));
}
// Extrai o bloco {{Nome ...}} balanceado a partir de uma posição.
function blocoTemplate(texto, nome) {
  const ini = texto.indexOf(`{{${nome}`);
  if (ini < 0) return '';
  let nivel = 0;
  for (let i = ini; i < texto.length; i++) {
    if (texto.startsWith('{{', i)) { nivel++; i++; continue; }
    if (texto.startsWith('}}', i)) { nivel--; i++; if (nivel === 0) return texto.slice(ini + nome.length + 2, i - 1); }
  }
  return '';
}

// ---------- Proficiências ----------
async function gerarProficiencias() {
  const tabelasTexto = await textoDaPagina('Weapon Proficiency Tables');
  const nomesTexto = await textoDaPagina('Template:Weapon Proficiency Name');
  const tabelas = {};
  const secoes = tabelasTexto.split(/^===\s*([^=].*?)\s*===\s*$/m);
  for (let i = 1; i < secoes.length; i += 2) {
    const nome = secoes[i].trim();
    const corpo = blocoTemplate(secoes[i + 1], 'Weapon Proficiency Table');
    if (!corpo) continue;
    if (tabelas[nome]) console.warn(`  aviso: tabela repetida "${nome}" (mantida a primeira)`);
    const niveis = [];
    for (let n = 1; n <= 7; n++) {
      const m = corpo.match(new RegExp(`(?:^|\\n)\\s*\\|\\s*perk_${n}\\s*=([\\s\\S]*?)(?=\\n\\s*\\|\\s*perk_\\d\\s*=|$)`));
      if (!m) continue;
      const botoes = [...m[1].matchAll(/\{\{Weapon Proficiency Button/g)].map((b) => {
        const p = parametros(blocoTemplate(m[1].slice(b.index), 'Weapon Proficiency Button'));
        return { t: (p.text || '').replace(/\[\[(?:[^|\]]*\|)?([^\]]*)\]\]/g, '$1'), i: p.skill_image || '' };
      }).filter((b) => b.t);
      if (botoes.length) niveis[n - 1] = botoes;
    }
    const lista = niveis.filter(Boolean);
    if (lista.length && !tabelas[nome]) tabelas[nome] = lista;
  }
  // #switch do Template:Weapon Proficiency Name: casos agrupados ("| A\n| B = Tabela") valem para todos.
  const armas = {};
  let pendentes = [];
  for (const linha of nomesTexto.split('\n')) {
    const m = linha.match(/^\s*\|\s*([^=#][^=]*?)\s*(?:=\s*(.+?))?\s*$/);
    if (!m) continue;
    pendentes.push(m[1].replace(/\s+/g, ' ').trim());
    if (m[2] !== undefined) {
      const tabela = m[2].replace(/\}\}\s*$/, '').replace(/\s+/g, ' ').trim();
      if (tabelas[tabela]) for (const a of pendentes) armas[a] = tabela;
      else if (tabela) console.warn(`  aviso: tabela inexistente "${tabela}" para ${pendentes.join(', ')}`);
      pendentes = [];
    }
  }
  return { armas, tabelas };
}

// ---------- Equipamentos ----------
const CATEGORIAS = {
  amulet: ['Amulets_and_Necklaces'], helmet: ['Helmets'], armor: ['Armors'], legs: ['Legs'], boots: ['Boots'], ring: ['Rings'],
  offhand: ['Shields', 'Spellbooks', 'Quivers'],
  weapon: ['Axe_Weapons', 'Club_Weapons', 'Sword_Weapons', 'Distance_Weapons', 'Wands', 'Rods', 'Fist_Fighting_Weapons'],
};
// "knights and paladins" → ["knight","paladin"]; vazio/"none" → todas; "without" sozinho → nenhuma vocação promovida.
function vocacoes(texto) {
  const t = String(texto || '').toLowerCase();
  if (!t || /\bnone\b|all vocations|any vocation/.test(t)) return [];
  const lista = ['knight', 'paladin', 'sorcerer', 'druid', 'monk'].filter((v) => t.includes(v));
  return lista.length ? lista : ['-'];
}
async function gerarEquipamentos() {
  const itens = [];
  const vistos = new Map();
  let descartados = 0;
  for (const [slot, categorias] of Object.entries(CATEGORIAS)) {
    const titulos = [];
    for (const c of categorias) titulos.push(...await membrosDaCategoria(c)); // sequencial, para não sobrecarregar a wiki
    const textos = await conteudos([...new Set(titulos)]);
    for (const [titulo, texto] of textos) {
      if (!/\{\{Infobox Object/i.test(texto)) continue;
      const nome = campo(texto, 'name') || titulo;
      const status = `${campo(texto, 'status')} ${campo(texto, 'deprecated')}`.toLowerCase();
      if (/deprecated|unavailable|yes/.test(status)) { descartados++; continue; }
      if (vistos.has(nome.toLowerCase())) { console.warn(`  aviso: "${nome}" aparece em ${vistos.get(nome.toLowerCase())} e ${slot} (mantido o primeiro)`); continue; }
      vistos.set(nome.toLowerCase(), slot);
      const item = { n: nome, s: slot };
      const extra = {
        l: Number(campo(texto, 'levelrequired')) || 0,
        vs: vocacoes(campo(texto, 'vocrequired')),
        a: campo(texto, 'attrib'),
        r: campo(texto, 'resist'),
        cc: campo(texto, 'crithit_ch'),
        cd: campo(texto, 'critextra_dmg'),
        u: Number(campo(texto, 'upgradeclass')) || 0,
        e: campo(texto, 'damagetype').toLowerCase(),
        k: slot === 'offhand' ? campo(texto, 'primarytype') : '',
      };
      for (const [k, v] of Object.entries(extra)) if (Array.isArray(v) ? v.length : v) item[k] = v;
      itens.push(item);
    }
    console.log(`  ${slot}: ${itens.filter((i) => i.s === slot).length} itens`);
  }
  if (descartados) console.log(`  ${descartados} itens descartados (deprecated/unavailable)`);
  itens.sort((a, b) => a.s.localeCompare(b.s) || (b.l || 0) - (a.l || 0) || a.n.localeCompare(b.n));
  return {
    // Chances por tier (páginas Onslaught e Transcendence da TibiaWiki).
    onslaught: [0, 0.5, 1.05, 1.7, 2.45, 3.3, 4.25, 5.3, 6.45, 7.7, 9.05],
    transcendence: [0, 0.13, 0.27, 0.44, 0.64, 0.86, 1.11, 1.38, 1.68, 2.0, 2.35],
    itens,
  };
}

// ---------- Execução: gera tudo em memória, confere e só então grava (de forma atômica) ----------
const geradoEm = new Date().toISOString();
const fonte = 'TibiaWiki (tibia.fandom.com) — CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0/), dados adaptados';
console.log('Proficiências…');
const prof = await gerarProficiencias();
console.log(`  ${Object.keys(prof.armas).length} armas, ${Object.keys(prof.tabelas).length} tabelas`);
console.log('Equipamentos…');
const equip = await gerarEquipamentos();
console.log(`  total: ${equip.itens.length} itens`);
const problemas = [];
if (Object.keys(prof.armas).length < 300) problemas.push('poucas armas com proficiência');
if (Object.keys(prof.tabelas).length < 50) problemas.push('poucas tabelas de proficiência');
if (equip.itens.length < 1000) problemas.push('poucos equipamentos');
for (const slot of Object.keys(CATEGORIAS)) if (!equip.itens.some((i) => i.s === slot)) problemas.push(`nenhum item no slot ${slot}`);
if (problemas.length) throw new Error(`Dados suspeitos, nada foi gravado: ${problemas.join('; ')}`);
await mkdir(SAIDA, { recursive: true });
for (const [arquivo, dados] of [['proficiencias.json', prof], ['equipamentos.json', equip]]) {
  const destino = join(SAIDA, arquivo);
  await writeFile(destino + '.tmp', JSON.stringify({ fonte, geradoEm, ...dados }));
  await rename(destino + '.tmp', destino);
}
console.log('Pronto:', geradoEm);
