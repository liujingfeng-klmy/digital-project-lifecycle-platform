import { ref } from 'vue'

/* 长文本折叠（#26 从 ProjectDetail.vue 抽出）。
   可研里的「建设内容 / 必要性 / 政策依据」动辄几千字，默认只列前 500 字；
   截断落在句号、分号或换行处，避免停在一句话中间。 */
const BLOCK_LIMIT = 500

export function useTextBlock() {
  const openBlocks = ref({})
  const isBlockOpen = (k) => !!openBlocks.value[k]
  const toggleBlock = (k) => { openBlocks.value[k] = !openBlocks.value[k] }
  const isLongText = (t) => (t || '').length > BLOCK_LIMIT

  function blockText(t, k) {
    const s = t || ''
    if (!s || isBlockOpen(k) || s.length <= BLOCK_LIMIT) return s
    const head = s.slice(0, BLOCK_LIMIT)
    const cut = Math.max(head.lastIndexOf('。'), head.lastIndexOf('；'), head.lastIndexOf('\n'))
    return (cut > BLOCK_LIMIT - 90 ? head.slice(0, cut + 1) : head) + '…'
  }

  return { isBlockOpen, toggleBlock, isLongText, blockText }
}
