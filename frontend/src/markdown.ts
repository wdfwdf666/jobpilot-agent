/**
 * Markdown 渲染（用于 AI 回复气泡）。
 *
 * 安全要点：v-html 渲染前必须过 DOMPurify 白名单净化，
 * 否则模型输出里被注入的 <script>/<img onerror> 会在用户浏览器执行（XSS）。
 * 代码块用 highlight.js 做高亮，按需注册语言以控制打包体积。
 */
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'
import { Marked } from 'marked'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)

const marked = new Marked({ gfm: true, breaks: true })

// marked v12+ 的 renderer.code 收到 token 对象；做一点兼容以稳妥处理
const renderer = {
  code(token: { text?: string; lang?: string; raw?: string }): string {
    const text = token.text ?? token.raw ?? ''
    const lang = (token.lang ?? '').trim().split(/\s+/)[0]
    let highlighted = ''
    let language = ''
    if (lang && hljs.getLanguage(lang)) {
      highlighted = hljs.highlight(text, { language: lang }).value
      language = lang
    } else {
      highlighted = hljs.highlightAuto(text).value
    }
    const cls = language ? ` class="hljs language-${language}"` : ' class="hljs"'
    return `<pre><code${cls}>${highlighted}</code></pre>`
  },
}

marked.use({ renderer })

/** 解析 + 净化，返回可直接用于 v-html 的 HTML。 */
export function renderMarkdown(src: string): string {
  const raw = marked.parse(src, { async: false }) as string
  return DOMPurify.sanitize(raw, {
    // 白名单之外的全部剔除；链接只允许 http/https/anchor
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
      'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'hr', 'span',
    ],
    ALLOWED_ATTR: ['href', 'class', 'target', 'rel'],
  })
}
