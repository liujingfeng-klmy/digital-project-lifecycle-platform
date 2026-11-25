<script setup>
/* 重复点逐项对照。
 *
 * 初审岗真正要回答的问题不是「像不像」，而是「哪一部分重复」：
 * 哪几项功能对得上、哪个章节在重复写、哪几段原文基本一致。
 * 所以这里不放总分，只把可指认的重合点摆出来，让初审不翻原文也能定位。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
  curName: { type: String, default: '本次申报' },
  prevName: { type: String, default: '历史项目' },
  // 比对范围：只比建设内容章。识别不到建设内容章时后端退回全文，这里要说明白，
  // 否则用户会把「全文比出来的分数」当成建设内容的相似度
  scope: { type: Object, default: () => ({}) },
})

const pct = (v) => Math.round((v || 0) * 100)

const pairs = computed(() => props.item.functions?.pairs || [])
const snips = computed(() => props.item.snippets || [])
const secs = computed(() => props.item.sections || [])
const findings = computed(() => props.item.findings || [])

// 按「本次材料」的章节分组：混在一张表里看不出这一项落在哪一章，
// 而落在建设内容章与落在安全保障章，性质完全不同。
// 建设内容类章节排在前面 —— 那才是重复建设的实质证据。
const TIERS = [
  { re: /建设内容|建设方案|建设目标|功能|需求/, tier: 0, label: '建设内容' },
  { re: /数据|共享|技术|实施|投资|概算|周期|运维|培训/, tier: 1, label: '配套章节' },
]

function tierOf(title) {
  const hit = TIERS.find((t) => t.re.test(title || ''))
  return hit || { tier: 2, label: '其他章节' }
}

const groups = computed(() => {
  const m = new Map()
  for (const p of pairs.value) {
    const k = (p.query_section || '').trim() || '章节未识别'
    if (!m.has(k)) m.set(k, [])
    m.get(k).push(p)
  }
  return [...m.entries()]
    .map(([title, list]) => {
      const t = tierOf(title)
      return {
        title,
        list,
        tier: t.tier,
        tierLabel: t.label,
        max: Math.max(...list.map((x) => x.score || 0)),
      }
    })
    .sort((a, b) => a.tier - b.tier || b.max - a.max)
})

const topSec = computed(() => {
  const arr = [...secs.value].sort((a, b) => (b.score || 0) - (a.score || 0))
  return arr[0] || null
})

const snipTop = computed(() =>
  snips.value.length ? Math.max(...snips.value.map((s) => s.score || 0)) : 0,
)

// 建设内容章的对上项数，是这个页面上最该先看的数字
const planCount = computed(
  () => pairs.value.filter((p) => /建设内容|建设方案|建设目标|需求/.test(p.query_section || '')).length,
)

const openSect = ref({})
function isOpen(i) {
  return openSect.value[i] !== false
}
</script>

<template>
  <div class="dc">
    <!-- 一屏看清重合到什么程度 -->
    <div class="dc-scope" :class="scope.fallback ? 'dc-scope-warn' : ''">
      <template v-if="scope.fallback">
        这份材料没有识别出「建设内容 / 需求分析」章（可能没分节，或标题不规范），
        <strong>以下按全文比对</strong>，分数里含通用章节，仅供参考，请人工复核。
      </template>
      <template v-else>
        只比「建设内容 / 建设方案 / 需求分析」章。必要性、政策依据、安全保障、
        测评方案、实施计划这些通用章节各项目本来就写得差不多，不参与比对。
      </template>
    </div>
    <div class="dc-chips">
      <span v-if="pairs.length" class="chip chip-hot">
        业务功能重合 {{ pct(item.functions?.overlap) }}%（{{ item.functions?.pair_count ?? pairs.length }}
        项 / 共 {{ item.functions?.business_q || 0 }} 项）
      </span>
      <span v-else class="chip chip-quiet">未对上业务功能（共 {{ item.functions?.business_q || 0 }} 项）</span>
      <span v-if="planCount" class="chip chip-hot">其中建设内容章 {{ planCount }} 项</span>
      <span v-if="topSec" class="chip">章节最像：{{ topSec.label }} {{ pct(topSec.score) }}%</span>
      <span v-if="snips.length" class="chip chip-warn">
        原文雷同 {{ snips.length }} 段<template v-if="snipTop">（最高 {{ pct(snipTop) }}%）</template>
      </span>
    </div>

    <!-- 功能逐项对照 -->
    <section v-if="pairs.length" class="dc-sec">
      <div class="dc-head">
        <span class="dc-h">功能逐项对照</span>
        <span class="dc-hint">
          左侧本次申报、右侧{{ prevName }}。同一条目出现在两侧，才叫功能重复
        </span>
      </div>

      <div class="dc-groups">
        <div v-for="(g, gi) in groups" :key="gi" class="dc-group">
          <div class="dc-group-head" @click="openSect[gi] = !isOpen(gi)">
            <span class="g-arrow">{{ isOpen(gi) ? '▾' : '▸' }}</span>
            <span class="g-title">{{ g.title }}</span>
            <span v-if="g.tier > 0" class="g-tier" :class="'tier-' + g.tier">{{ g.tierLabel }}</span>
            <span class="g-spacer" />
            <span class="g-count">{{ g.list.length }} 项</span>
            <span class="g-max">最高 {{ pct(g.max) }}%</span>
          </div>
          <table v-show="isOpen(gi)" class="dc-table">
            <thead>
              <tr>
                <th class="c1">{{ curName }}</th>
                <th class="c2">相似</th>
                <th class="c3">{{ prevName }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(p, i) in g.list" :key="i">
                <td class="c1">
                  <div class="fn">{{ p.query }}</div>
                  <div class="fs">{{ p.query_section || '—' }}</div>
                </td>
                <td class="c2">
                  <div class="sim">
                    <span class="sim-bar" :style="{ width: Math.max(6, pct(p.score)) + '%' }" />
                    <em>{{ pct(p.score) }}%</em>
                  </div>
                </td>
                <td class="c3">
                  <div class="fn">{{ p.hit }}</div>
                  <div class="fs">{{ p.hit_section || '—' }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="item.functions?.pair_count > pairs.length" class="dc-trunc">
        共 {{ item.functions.pair_count }} 项对得上，按相似度从高到低列出前 {{ pairs.length }} 项；
        其余以档案库的完整比对报告为准。
      </div>

      <div v-if="item.functions?.generic_matched?.length" class="dc-generic">
        <span class="g-label">通用能力重合</span>
        <span v-for="g in item.functions.generic_matched" :key="g" class="g-tag">{{ g }}</span>
        <div class="g-note">门户、认证、共享交换这类共性能力本来就该复用，不作为重复依据。</div>
      </div>
    </section>

    <!-- 两边都抽不出东西时，只留一句说明，不占版面 -->
    <div v-if="!pairs.length && !snips.length" class="dc-empty dc-empty-lead">
      建设内容章里没有对上功能条目、也没有逐字相同的段落。
      这不等于不重复 —— 换名改造、把同一件事写成另一种说法，机器认不出来，
      仍需人工翻阅两份材料的建设内容章。
    </div>

    <!-- 章节对照 -->
    <section v-if="secs.length" class="dc-sec">
      <div class="dc-head">
        <span class="dc-h">建设内容章相似度</span>
        <span class="dc-hint">只对建设内容与需求分析两章；通用章节不比，比了也是人人高分</span>
      </div>
      <div class="dc-secs">
        <div v-for="s in secs" :key="s.category" class="sec-row">
          <span class="sec-name">{{ s.label }}</span>
          <span class="sec-track">
            <span class="sec-fill" :style="{ width: Math.max(2, pct(s.score)) + '%' }" />
          </span>
          <span class="sec-val">{{ pct(s.score) }}%</span>
          <span class="sec-chars">{{ (s.query_chars || 0).toLocaleString() }} / {{ (s.hit_chars || 0).toLocaleString() }} 字</span>
        </div>
      </div>
    </section>

    <!-- 原文对照 -->
    <section v-if="snips.length || pairs.length" class="dc-sec">
      <div class="dc-head">
        <span class="dc-h">原文段落对照</span>
        <span class="dc-hint">
          左右同位置的文字基本一致，就是照抄，不必再翻原件；相似度低但意思雷同的，仍需人工判断
        </span>
      </div>

      <div v-if="!snips.length" class="dc-empty">
        建设内容章里没有逐字相同的段落。段落相似要 42% 以上的字词重合才列出，
        低于这个线通常是改写而非照抄 —— 改写的段落仍需人工判断。
      </div>

      <div v-else class="snips">
        <div v-for="(sn, i) in snips" :key="i" class="snip">
          <div class="snip-head">
            <span class="snip-score" :class="sn.score >= 0.8 ? 'hot' : ''">{{ pct(sn.score) }}%</span>
            <span class="snip-sect">{{ sn.query_section || '—' }} ↔ {{ sn.hit_section || '—' }}</span>
          </div>
          <div class="snip-body">
            <div class="sn-col">
              <div class="sn-tag">{{ curName }}</div>
              <p>{{ sn.query_text }}</p>
            </div>
            <div class="sn-col">
              <div class="sn-tag">{{ prevName }}</div>
              <p>{{ sn.hit_text }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 疑点 -->
    <section v-if="findings.length" class="dc-sec">
      <div class="dc-head">
        <span class="dc-h">疑点与核实要点</span>
        <span class="dc-hint">系统只提问，不下结论</span>
      </div>
      <div v-for="(f, i) in findings" :key="i" class="fd" :class="'fd-' + f.level">
        <div class="fd-head">
          <span class="fd-level">{{ f.level_label }}</span>
          <span class="fd-title">{{ f.title }}</span>
        </div>
        <p class="fd-detail">{{ f.detail }}</p>
        <p v-if="f.ask" class="fd-ask"><span class="ask-tag">请核实</span>{{ f.ask }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dc { font-size: 12.5px; }

/* 概览 */
.dc-scope {
  font-size: 11.5px; line-height: 1.8; color: var(--ink-3);
  background: var(--panel-2); border-radius: 7px;
  padding: 7px 10px; margin-bottom: 10px;
}
.dc-scope-warn { background: var(--warn-bg); color: var(--warn); }
.dc-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.chip {
  font-size: 11.5px; padding: 2px 8px; border-radius: 10px;
  background: var(--panel-2); color: var(--ink-2); border: 1px solid var(--line-soft);
}
.chip-hot { background: var(--danger-bg); color: var(--danger); border-color: #F1C6C2; }
.chip-warn { background: var(--warn-bg); color: var(--warn); border-color: #F3DDC2; }
.chip-quiet { color: var(--ink-3); }

.dc-sec { margin-bottom: 14px; }
.dc-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 7px; }
.dc-h { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.dc-hint { font-size: 12px; color: var(--ink-4); }
.dc-empty {
  padding: 9px 11px; border-radius: 7px; background: var(--panel-2);
  color: var(--ink-3); font-size: 12px; line-height: 1.8;
}
.dc-empty-lead { margin-bottom: 12px; }

/* 功能分组 */
.dc-groups { display: flex; flex-direction: column; gap: 6px; }
.dc-group { border: 1px solid var(--line-soft); border-radius: 8px; overflow: hidden; }
.dc-group-head {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; background: var(--panel-2); cursor: pointer;
}
.dc-group-head:hover { background: #F8FBFF; }
.g-arrow { font-size: 10px; color: var(--ink-4); width: 8px; }
.g-title { font-size: 12px; color: var(--ink); }
.g-spacer { flex: 1; }
.g-tier {
  font-size: 10px; padding: 0 5px; border-radius: 3px;
  background: var(--surface-3); color: var(--ink-3);
}
.g-tier.tier-0 { background: var(--danger-bg); color: var(--danger); }
.g-tier.tier-1 { background: var(--brand-bg); color: var(--brand-deep); }
.g-count { font-size: 12px; color: var(--brand-deep); }
.g-max { font-size: 12px; color: var(--ink-4); }
.dc-trunc { margin-top: 6px; font-size: 12px; color: var(--ink-4); line-height: 1.7; }

.dc-table { width: 100%; border-collapse: collapse; }
.dc-table th {
  font-weight: 400; font-size: 12px; color: var(--ink-4);
  text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line-soft);
  background: #FFFFFF;
}
.dc-table td { padding: 6px 8px; border-bottom: 1px dashed var(--line-soft); vertical-align: top; }
.dc-table tr:last-child td { border-bottom: 0; }
.c1 { width: 40%; }
.c2 { width: 90px; }
.c3 { width: auto; }
.fn { color: var(--ink); line-height: 1.6; }
.fs { color: var(--ink-4); font-size: 12px; margin-top: 2px; }
.sim { display: flex; align-items: center; gap: 5px; }
.sim-bar { display: inline-block; height: 4px; border-radius: 2px; background: var(--danger); opacity: .55; }
.sim em { font-style: normal; font-size: 12px; color: var(--ink-3); }

.dc-generic { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.g-label { font-size: 12px; color: var(--ink-3); margin-right: 2px; }
.g-tag {
  font-size: 12px; padding: 1px 7px; border-radius: 4px;
  background: var(--panel-2); color: var(--ink-3); border: 1px solid var(--line-soft);
}
.g-note { flex: 1 0 100%; font-size: 12px; color: var(--ink-4); line-height: 1.7; margin-top: 2px; }

/* 章节 */
.dc-secs { display: flex; flex-direction: column; gap: 3px; }
.sec-row { display: flex; align-items: center; gap: 8px; }
.sec-name { flex: 0 0 108px; color: var(--ink-2); font-size: 11.5px; }
.sec-track { flex: 1; height: 5px; border-radius: 3px; background: var(--line-soft); overflow: hidden; }
.sec-fill { display: block; height: 100%; background: var(--brand); opacity: .55; }
.sec-val { flex: 0 0 34px; text-align: right; font-size: 12px; color: var(--ink-3); }
.sec-chars { flex: 0 0 112px; text-align: right; font-size: 12px; color: var(--ink-4); }

/* 原文对照 */
.snips { display: flex; flex-direction: column; gap: 8px; }
.snip { border: 1px solid var(--line-soft); border-radius: 8px; overflow: hidden; }
.snip-head {
  display: flex; align-items: center; gap: 10px;
  padding: 5px 10px; background: var(--panel-2);
}
.snip-score { font-size: 11.5px; font-weight: 600; color: var(--brand); }
.snip-score.hot { color: var(--danger); }
.snip-sect { font-size: 12px; color: var(--ink-4); }
.snip-body { display: flex; }
.sn-col { flex: 1; min-width: 0; padding: 9px 11px; }
.sn-col + .sn-col { border-left: 1px solid var(--line-soft); }
.sn-tag { font-size: 12px; color: var(--ink-4); margin-bottom: 4px; }
.sn-col p { margin: 0; font-size: 12px; line-height: 1.85; color: var(--ink-2); }

/* 疑点 */
.fd {
  border: 1px solid var(--line-soft); border-left-width: 3px;
  border-radius: 7px; padding: 8px 11px; margin-bottom: 6px; background: #fff;
}
.fd-attention { border-left-color: var(--danger); background: #FDF6F6; }
.fd-verify { border-left-color: var(--warn); background: #FFFCF7; }
.fd-info { border-left-color: var(--brand); }
.fd-head { display: flex; align-items: center; gap: 8px; }
.fd-level {
  font-size: 12px; padding: 1px 6px; border-radius: 4px;
  background: var(--panel-2); color: var(--ink-2);
}
.fd-attention .fd-level { background: var(--danger-bg); color: var(--danger); }
.fd-verify .fd-level { background: var(--warn-bg); color: var(--warn); }
.fd-title { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.fd-detail { margin: 5px 0 0; color: var(--ink-2); line-height: 1.8; }
.fd-ask {
  margin: 6px 0 0; padding: 6px 9px; border-radius: 6px;
  background: rgba(24, 95, 165, .06); color: var(--ink-2); line-height: 1.8;
}
.ask-tag {
  display: inline-block; margin-right: 6px; font-size: 12px;
  color: var(--brand-deep); background: var(--brand-bg);
  padding: 1px 5px; border-radius: 4px;
}
</style>
