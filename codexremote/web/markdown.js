// Local, HTML-disabled Markdown with math parsed before Markdown escaping.
export function createMarkdown({linkMarkup, escape}) {
  const md = window.markdownit({html: false, linkify: true, breaks: true});
  const escapedAt = (text, pos) => {
    let n = 0;
    while (pos > 0 && text[--pos] === '\\') n++;
    return n % 2 === 1;
  };
  function closing(text, delimiter, start, singleDollar = false) {
    for (let p = text.indexOf(delimiter, start); p !== -1; p = text.indexOf(delimiter, p + delimiter.length)) {
      if (escapedAt(text, p)) continue;
      if (singleDollar && (/\s/.test(text[p - 1]) || /[\d$]/.test(text[p + 1] || '') || text[p - 1] === '$')) continue;
      return p;
    }
    return -1;
  }
  function math(source, display) {
    try {
      return window.katex.renderToString(source, {displayMode: display, throwOnError: false,
        trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20, errorColor: '#a0a0a0'});
    } catch {
      return '<code>' + escape(source) + '</code>';
    }
  }
  md.inline.ruler.before('escape', 'math', (state, silent) => {
    const start = state.pos, text = state.src;
    const open = ['$$', '\\[', '\\(', '$'].find(d => text.startsWith(d, start));
    if (!open) return false;
    const single = open === '$', display = open === '$$' || open === '\\[';
    if (single && (/\s/.test(text[start + 1] || ' ') || text[start - 1] === '$')) return false;
    const close = open === '\\[' ? '\\]' : open === '\\(' ? '\\)' : open;
    const end = closing(text, close, start + open.length, single);
    if (end === -1 || end >= state.posMax || (single && text.slice(start, end).includes('\n'))) return false;
    if (!silent) {
      const token = state.push('math_inline', '', 0);
      token.content = text.slice(start + open.length, end);
      token.meta = {display};
    }
    state.pos = end + close.length;
    return true;
  });
  // Keep standalone display blocks out of paragraphs, including multiline aligned equations.
  md.block.ruler.before('fence', 'math_block', (state, start, end, silent) => {
    if (state.sCount[start] - state.blkIndent >= 4) return false;
    const first = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]);
    const open = first.startsWith('$$') ? '$$' : first.startsWith('\\[') ? '\\[' : null;
    if (!open) return false;
    const close = open === '$$' ? '$$' : '\\]';
    let source = first.slice(2), line = start;
    let finish = closing(source, close, 0);
    while (finish === -1 && ++line < end) {
      source += '\n' + state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
      finish = closing(source, close, 0);
    }
    if (finish === -1 || source.slice(finish + 2).trim()) return false;
    if (silent) return true;
    const token = state.push('math_block', '', 0);
    token.block = true;
    token.content = source.slice(0, finish);
    token.map = [start, line + 1];
    state.line = line + 1;
    return true;
  }, {alt: ['paragraph', 'reference', 'blockquote', 'list']});
  md.renderer.rules.math_inline = (tokens, i) => math(tokens[i].content, tokens[i].meta.display);
  md.renderer.rules.math_block = (tokens, i) => math(tokens[i].content, true) + '\n';
  md.renderer.rules.link_open = (tokens, i) => {
    const markup = linkMarkup('', tokens[i].attrGet('href'));
    tokens[i].meta = {hasLink: markup.startsWith('<a ')};
    return tokens[i].meta.hasLink ? markup.slice(0, markup.indexOf('>') + 1) : '';
  };
  md.renderer.rules.link_close = (tokens, i) => {
    for (let j = i - 1; j >= 0; j--) if (tokens[j].type === 'link_open') return tokens[j].meta?.hasLink ? '</a>' : '';
    return '';
  };
  md.renderer.rules.image = (tokens, i) => linkMarkup(tokens[i].content, tokens[i].attrGet('src'), true);
  md.renderer.rules.fence = (tokens, i) => '<div class="code-wrap"><button class="copy-code">Copy</button><pre><code>' + escape(tokens[i].content.replace(/\n$/, '')) + '</code></pre></div>\n';
  const renderText = md.renderer.rules.text;
  md.renderer.rules.text = (tokens, i, options, env, self) => {
    if (i === 0 && env.inList && /^\[[ xX]\] /.test(tokens[i].content)) {
      return '<input class="task-checkbox" type="checkbox" disabled' + (/^\[[xX]\]/.test(tokens[i].content) ? ' checked' : '') + '> ' + escape(tokens[i].content.slice(4));
    }
    return renderText(tokens, i, options, env, self);
  };
  md.renderer.rules.list_item_open = (_, __, ___, env) => { env.inList = (env.inList || 0) + 1; return '<li>'; };
  md.renderer.rules.list_item_close = (_, __, ___, env) => { env.inList--; return '</li>\n'; };
  return text => md.render(String(text ?? ''), {});
}
