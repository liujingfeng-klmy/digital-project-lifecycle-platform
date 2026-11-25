<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { archiveApi } from '@/api'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  report: { type: Object, required: true },
})

const userStore = useUserStore()
const canReview = computed(() =>
  ['reviewer', 'expert', 'auditor', 'admin'].includes(userStore.role),
)

const open = ref({ 0: true })
const showMore = reactive({})

// 提交后即时回填，不必重跑比对
const localReviews = reactive({})
const editing = reactive({})
const saving = reactive({})
const form = reactive({})

// 换一份材料重新比对时，表单与临时核定都要重置，
// 否则会拿着上一次的项目号提交，把核定记到别人名下
watch(
  () => props.report,
  (r) => {
    Object.keys(form).forEach((k) => delete form[k])
    Object.keys(localReviews).forEach((k) => delete localReviews[k])
    Object.keys(editing).forEach((k) => delete editing[k])
    ;(r?.ranking || []).forEach((x) => {
      form[x.project_id] = { verdict: '', basis: '' }
    })
    open.value = { 0: true }
  },
  { immediate: true },
)

const pct = (v) => Math.round((v || 0) * 100)

const attention = computed(() => props.report.headline?.attention || 0)
const verify = computed(() => props.report.headline?.verify || 0)
const pending = computed(() => attention.value + verify.value)

const headLevel = computed(() => {
  if (attention.value) return 'attention'
  if (verify.value) return 'verify'
  return 'none'
})

const headTitle = computed(() => {
  if (attention.value) return `有 ${attention.value} 项需要重点关注`
  if (verify.value) return `有 ${verify.value} 项需要核实`
  if (props.report.ranking?.length) return '没有筛出需要核实的疑点'
  return '没有可比对的历史材料'
})

function toggle(i) {
  open.value[i] = !open.value[i]
}

function reviewOf(item) {
  return localReviews[item.project_id] || item.review || null
}

function fmtTime(s) {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleString('zh-CN', { hour12: false }).slice(0, 16)
}

async function submit(item) {
  const f = form[item.project_id]
  if (!f.verdict) {
    ElMessage.warning('请先选择核定结论')
    return
  }
  const basis = (f.basis || '').trim()
  if (f.verdict !== 'need_more' && basis.length < 10) {
    ElMessage.warning('核定依据至少 10 个字，要写明比对了什么、为什么这样认定')
    return
  }
  saving[item.project_id] = true
  try {
    const { data } = await archiveApi.reviewCompare({
      query_hash: props.report.query.hash,
      query_name: props.report.query.name,
      query_unit: props.report.query.unit,
      query_year: props.report.query.year,
      project: item.project_id,
      verdict: f.verdict,
      basis,
      findings: item.findings || [],
    })
    localReviews[item.project_id] = data
    editing[item.project_id] = false
    f.verdict = ''
    f.basis = ''
    ElMessage.success('核定已记录')
  } catch (e) {
    const msg = e?.response?.data
    if (msg?.basis) ElMessage.error(String(msg.basis))
    else if (msg?.detail) ElMessage.error(msg.detail)
    else ElMessage.error('提交失败，请检查权限或稍后重试')
  } finally {
    saving[item.project_id] = false
  }
}
</script>

<template>
  <div class="report">
    <!-- 头部：只说疑点数量，不说是否重复 -->
    <div class="headline" :class="'hl-' + headLevel">
      <div class="hl-count">
        <b>{{ pending }}</b>
        <span>项疑点待核定</span>
      </div>
      <div class="hl-main">
        <div class="hl-title">{{ headTitle }}</div>
        <p class="hl-note">
          系统只把可疑的地方摆出来，<b>不判定是否重复</b>。是否构成重复建设，
          由专家调阅两份材料后核定，核定意见与依据记录在案。
        </p>
        <div class="hl-meta">
          本次材料 {{ report.query.chars }} 字 · {{ report.query.sections }} 章 ·
          {{ report.query.functions }} 项业务功能
          <span v-if="report.query.is_upgrade" class="upgrade-flag">材料含改造／升级表述</span>
          <br />
          比对 {{ report.summary.compared }} 个历史项目（{{ report.summary.scanned_files }} 份材料）·
          共 {{ report.summary.findings }} 条疑点 ·
          已核定 {{ report.reviewed || 0 }} 项 ·
          耗时 {{ report.summary.elapsed_ms }} 毫秒
        </div>
      </div>
    </div>

    <div v-if="report.self_reference?.matched" class="self-ref">
      这份材料本身已经在历史档案库里（{{ report.self_reference.projects.join('、') }}），
      比对时已把它自己排除，否则会跟自己撞出 100%。下面是与其他项目的比较。
    </div>

    <div class="disclaimer">
      疑点由规则筛出，<b>只说明「值得看一眼」，不说明「就是重复」</b>。
      材料写得虚、功能名称换了个说法，都可能筛不出来；因此
      「未筛出疑点」也不等于不重复。最终意见以专家核定为准。
    </div>

    <!-- 候选历史项目 -->
    <div class="rank">
      <div class="rank-title">与历史项目的比对（疑点多的排在前面，不按分数排）</div>
      <div v-if="!report.ranking.length" class="empty">
        历史档案库为空，或没有解析成功的材料可对比。此情形不能视为「无重复」。
      </div>

      <div v-for="(item, i) in report.ranking" :key="item.project_id" class="item">
        <div class="item-head" @click="toggle(i)">
          <span class="idx">{{ i + 1 }}</span>
          <div class="item-name">
            <div class="name-line">{{ item.project_name }}</div>
            <div class="sub-line">
              {{ item.unit || '单位未填' }} · {{ item.year || '年度未填' }} ·
              {{ item.investment ? item.investment + ' 万元' : '投资未填' }} ·
              {{ item.doc_type }}
            </div>
          </div>
          <div class="head-metrics">
            <span class="metric-key">疑点</span>
            <span class="metric-val" :class="{ zero: !item.screen.total }">{{ item.screen.total }}</span>
          </div>
          <span class="item-badge" :class="'bd-' + item.screen.level">
            {{ item.screen.level === 'none' ? '未筛出疑点' : item.screen.level_label }}
          </span>
          <span v-if="reviewOf(item)" class="rv-flag" :class="'vf-' + reviewOf(item).verdict">
            {{ reviewOf(item).verdict_label }}
          </span>
        </div>

        <div v-show="open[i]" class="item-body">
          <!-- 疑点清单：报告的主体 -->
          <div v-if="item.findings?.length" class="findings">
            <div
              v-for="(f, k) in item.findings"
              :key="k"
              class="finding"
              :class="'f-' + f.level"
            >
              <div class="f-head">
                <span class="f-level">{{ f.level_label }}</span>
                <span class="f-title">{{ f.title }}</span>
              </div>
              <p class="f-detail">{{ f.detail }}</p>
              <p v-if="f.ask" class="f-ask"><span class="ask-tag">请核实</span>{{ f.ask }}</p>
              <div v-if="f.evidence?.length" class="f-ev">
                <span class="ev-label">依据</span>
                <template v-for="(e, n) in f.evidence.slice(0, 6)" :key="n">
                  <span v-if="e.query" class="ev-pair">
                    {{ e.query }} <em>↔</em> {{ e.hit }}
                    <i v-if="e.score">{{ pct(e.score) }}%</i>
                  </span>
                  <span v-else-if="e.generic" class="ev-pair">{{ e.generic }}</span>
                </template>
              </div>
            </div>
          </div>
          <div v-else class="no-finding">
            按现有材料未筛出需要核实的疑点。这只说明规则没发现异常，
            不代表不重复——如果两份材料都写得很概括，建议人工翻一遍建设内容章。
          </div>

          <!-- 功能重合清单 -->
          <div class="block">
            <div class="block-title">
              功能重合明细
              <span class="block-hint">
                本次 {{ item.functions.business_q }} 项 · 历史 {{ item.functions.business_d }} 项 ·
                对得上 {{ item.functions.pairs.length }} 项（{{ pct(item.functions.overlap) }}）
              </span>
            </div>
            <table v-if="item.functions.pairs.length" class="func-table">
              <thead>
                <tr>
                  <th>本次申报的功能</th>
                  <th class="col-score">相似度</th>
                  <th>历史项目已有的功能</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(p, k) in item.functions.pairs" :key="k">
                  <td class="fq">{{ p.query }}</td>
                  <td class="col-score">
                    <span class="bar" :style="{ width: Math.max(8, pct(p.score)) + '%' }" />
                    <em>{{ pct(p.score) }}</em>
                  </td>
                  <td class="fh">{{ p.hit }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="no-func">
              两份材料都没有抽到可比对的功能清单（建设内容可能写得比较概括）。
              功能层无从判断，需人工翻阅原文。
            </div>
          </div>

          <div class="duo">
            <div class="block">
              <div class="block-title">通用能力重合</div>
              <div v-if="item.functions.generic_matched.length" class="generic">
                <el-tag
                  v-for="g in item.functions.generic_matched"
                  :key="g"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ g }}
                </el-tag>
                <p class="generic-note">
                  门户、认证、共享交换这类共性能力，名字相同是常态，可复用但不作为重复依据。
                </p>
              </div>
              <div v-else class="muted">未发现通用能力重合。</div>
            </div>

            <div class="block">
              <div class="block-title">业务领域对照</div>
              <div class="domain-row">
                <span class="dom-label">本次</span>
                <span class="dom-val">{{ item.domain.q_domain || '未能识别' }}</span>
              </div>
              <div class="domain-row">
                <span class="dom-label">历史</span>
                <span class="dom-val">{{ item.domain.d_domain || '未能识别' }}</span>
              </div>
              <div class="domain-row">
                <span class="dom-label">共有对象</span>
                <span class="dom-val">
                  {{ item.domain.shared_objects?.length
                    ? item.domain.shared_objects.slice(0, 8).join('、')
                    : '无' }}
                </span>
              </div>
              <p v-if="!item.domain.same" class="domain-note">
                业务对象不搭界。此类情况即使功能名称相近，也多半不是同一件事。
              </p>
            </div>
          </div>

          <!-- 专家核定 -->
          <div class="review-box">
            <div class="rv-head">
              <span class="rv-title">专家核定</span>
              <span class="rv-hint">核定后计入台账，作为预审／评审的留痕材料</span>
            </div>

            <template v-if="reviewOf(item) && !editing[item.project_id]">
              <div class="rv-done">
                <span class="rv-verdict" :class="'vf-' + reviewOf(item).verdict">
                  {{ reviewOf(item).verdict_label }}
                </span>
                <span class="rv-by">
                  {{ reviewOf(item).reviewer_display || '未署名' }} ·
                  {{ fmtTime(reviewOf(item).created) }}
                </span>
              </div>
              <p v-if="reviewOf(item).basis" class="rv-basis">{{ reviewOf(item).basis }}</p>
              <el-button
                v-if="canReview"
                size="small"
                text
                type="primary"
                @click="editing[item.project_id] = true"
              >
                重新核定
              </el-button>
            </template>

            <template v-else-if="canReview">
              <el-radio-group v-model="form[item.project_id].verdict" size="small" class="rv-choices">
                <el-radio-button label="duplicate">构成重复建设</el-radio-button>
                <el-radio-button label="not_duplicate">不构成重复建设</el-radio-button>
                <el-radio-button label="need_more">需补材料后再定</el-radio-button>
              </el-radio-group>
              <el-input
                v-model="form[item.project_id].basis"
                type="textarea"
                :rows="2"
                maxlength="600"
                show-word-limit
                placeholder="写清比对了哪几份材料、看了哪些功能、为什么这样认定（不少于 10 字）"
              />
              <div class="rv-actions">
                <el-button
                  size="small"
                  type="primary"
                  :loading="saving[item.project_id]"
                  @click="submit(item)"
                >
                  提交核定
                </el-button>
                <el-button
                  v-if="reviewOf(item)"
                  size="small"
                  text
                  @click="editing[item.project_id] = false"
                >
                  取消
                </el-button>
              </div>
            </template>

            <div v-else class="rv-readonly">
              疑点由初审岗、评审专家、审核岗核定。你当前是{{ userStore.user?.role_label || '申报单位' }}，
              可以查看比对结果，但不能为自己申报的项目下结论。
            </div>
          </div>

          <!-- 参考数据 -->
          <div class="more" @click="showMore[i] = !showMore[i]">
            {{ showMore[i] ? '收起参考数据' : '展开参考数据（文本相似度、章节对照、相似段落）' }}
          </div>

          <div v-show="showMore[i]" class="more-body">
            <div class="duo">
              <div class="block">
                <div class="block-title">文本相似度（参考，不作判定依据）</div>
                <div class="score-row"><span>全文余弦</span><b>{{ pct(item.scores.cosine) }}</b></div>
                <div class="score-row"><span>内容包含度</span><b>{{ pct(item.scores.containment) }}</b></div>
                <div class="score-row"><span>章节相似度</span><b>{{ pct(item.scores.section) }}</b></div>
                <div class="score-row"><span>元数据接近度</span><b>{{ pct(item.scores.meta) }}</b></div>
                <div class="score-row total"><span>综合相关度</span><b>{{ pct(item.scores.relevance) }}</b></div>
                <p class="score-note">
                  相关度只用于给候选项目排序，不是「重复程度」，更不是结论。
                </p>
              </div>
              <div class="block">
                <div class="block-title">章节对照</div>
                <table class="sec-table">
                  <tr v-for="s in item.sections" :key="s.category">
                    <td class="sec-name">{{ s.label }}</td>
                    <td class="sec-bar"><span :style="{ width: Math.max(2, pct(s.score)) + '%' }" /></td>
                    <td class="sec-val">{{ pct(s.score) }}%</td>
                  </tr>
                </table>
              </div>
            </div>

            <div v-if="item.notes?.length" class="notes">
              <div v-for="n in item.notes" :key="n">· {{ n }}</div>
            </div>

            <div v-if="item.snippets?.length" class="snippets">
              <div class="block-title">文本相似段落（左右对照，供翻阅原文时定位）</div>
              <div v-for="(sn, k) in item.snippets" :key="k" class="snip">
                <div class="snip-head">
                  <span class="snip-score">{{ pct(sn.score) }}%</span>
                  <span>{{ sn.query_section }} ↔ {{ sn.hit_section }}</span>
                </div>
                <div class="snip-body">
                  <div class="col">
                    <div class="col-tag">本次材料</div>
                    <p>{{ sn.query_text }}</p>
                  </div>
                  <div class="col">
                    <div class="col-tag">历史材料</div>
                    <p>{{ sn.hit_text }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="foot-note">
      核定记录只增不改：核定错了应当重新核定一条，前一条保留，便于回溯是谁在什么时点、
      基于哪几条疑点作出的判断。
    </div>
  </div>
</template>

<style scoped>
.report { font-size: 13px; }

/* 头部 */
.headline {
  display: flex;
  gap: 20px;
  align-items: center;
  padding: 18px 20px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--panel);
}
.headline.hl-attention { background: var(--danger-bg); border-color: #F1C6C2; }
.headline.hl-verify { background: var(--warn-bg); border-color: #F3DDC2; }
.headline.hl-none { background: var(--ok-bg); border-color: #BCE5D2; }

.hl-count { flex: 0 0 96px; text-align: center; }
.hl-count b { display: block; font-size: 34px; font-weight: 600; line-height: 1.05; letter-spacing: -1px; }
.hl-attention .hl-count b { color: var(--danger); }
.hl-verify .hl-count b { color: var(--warn); }
.hl-none .hl-count b { color: var(--ok); }
.hl-count span { font-size: 12px; color: var(--ink-3); }

.hl-main { min-width: 0; }
.hl-title { font-size: 14px; font-weight: 600; color: var(--ink); }
.hl-note { margin: 6px 0 5px; color: var(--ink-2); line-height: 1.8; }
.hl-note b { color: var(--ink); font-weight: 600; }
.hl-meta { font-size: 11.5px; color: var(--ink-3); line-height: 1.9; }
.upgrade-flag {
  display: inline-block;
  margin-left: 8px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12px;
}

.disclaimer {
  margin: 10px 0 18px;
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.8;
}
.disclaimer b { color: var(--ink-2); font-weight: 500; }

.self-ref {
  margin: 10px 0 0;
  padding: 8px 12px;
  border-radius: 7px;
  background: var(--brand-bg);
  color: var(--brand-deep);
  font-size: 11.5px;
  line-height: 1.8;
}

/* 列表 */
.rank-title { font-size: 12.5px; color: var(--ink-2); margin-bottom: 8px; }
.empty { color: var(--ink-3); padding: 20px 0; line-height: 1.9; }

.item {
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--panel);
  margin-bottom: 8px;
  overflow: hidden;
}
.item-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  cursor: pointer;
}
.item-head:hover { background: var(--panel-2); }
.idx {
  width: 20px; height: 20px; flex: 0 0 20px;
  border-radius: 5px; background: var(--panel-2);
  color: var(--ink-3); font-size: 12px;
  display: flex; align-items: center; justify-content: center;
}
.item-name { flex: 1; min-width: 0; }
.name-line { font-size: 13.5px; }
.sub-line { font-size: 11.5px; color: var(--ink-3); margin-top: 2px; }
.head-metrics { text-align: right; }
.metric-key { font-size: 12px; color: var(--ink-4); margin-right: 4px; }
.metric-val { font-size: 15px; font-weight: 600; color: var(--brand); }
.metric-val.zero { color: var(--ink-4); }

.item-badge {
  font-size: 11.5px;
  padding: 3px 8px;
  border-radius: 5px;
  white-space: nowrap;
}
.bd-attention { color: var(--danger); background: var(--danger-bg); }
.bd-verify { color: var(--warn); background: var(--warn-bg); }
.bd-info { color: var(--brand-deep); background: var(--brand-bg); }
.bd-none { color: var(--ink-3); background: var(--panel-2); }

.rv-flag {
  font-size: 12px;
  padding: 3px 7px;
  border-radius: 5px;
  white-space: nowrap;
  border: 1px solid transparent;
}

.item-body { padding: 10px 14px 16px; border-top: 1px solid var(--line-soft); }

/* 疑点 */
.findings { margin-bottom: 14px; }
.finding {
  border: 1px solid var(--line-soft);
  border-left-width: 3px;
  border-radius: 7px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: #fff;
}
.finding.f-attention { border-left-color: var(--danger); background: #FDF6F6; }
.finding.f-verify { border-left-color: var(--warn); background: #FFFCF7; }
.finding.f-info { border-left-color: var(--brand); background: var(--panel); }

.f-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.f-level {
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--panel-2);
  color: var(--ink-2);
}
.f-attention .f-level { background: var(--danger-bg); color: var(--danger); }
.f-verify .f-level { background: var(--warn-bg); color: var(--warn); }
.f-info .f-level { background: var(--brand-bg); color: var(--brand-deep); }
.f-title { font-size: 13px; font-weight: 600; color: var(--ink); }
.f-detail { margin: 6px 0 0; color: var(--ink-2); line-height: 1.85; font-size: 12.5px; }

.f-ask {
  margin: 7px 0 0;
  padding: 7px 10px;
  border-radius: 6px;
  background: rgba(24, 95, 165, 0.06);
  color: var(--ink-2);
  line-height: 1.8;
  font-size: 12.5px;
}
.ask-tag {
  display: inline-block;
  margin-right: 6px;
  font-size: 12px;
  color: var(--brand-deep);
  background: var(--brand-bg);
  padding: 1px 5px;
  border-radius: 4px;
}

.f-ev {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.9;
}
.ev-label { margin-right: 6px; color: var(--ink-4); }
.ev-pair { display: inline-block; margin-right: 10px; }
.ev-pair em { font-style: normal; color: var(--ink-4); }
.ev-pair i { font-style: normal; color: var(--brand); margin-left: 3px; }

.no-finding {
  padding: 10px 12px;
  border-radius: 7px;
  background: var(--panel-2);
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.85;
  margin-bottom: 14px;
}

/* 通用块 */
.block { margin-bottom: 14px; min-width: 0; }
.block-title { font-size: 12px; color: var(--ink-3); margin-bottom: 6px; }
.block-hint { color: var(--ink-4); font-size: 12px; margin-left: 6px; }

.func-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.func-table th {
  text-align: left;
  font-weight: 400;
  font-size: 12px;
  color: var(--ink-4);
  padding: 4px 6px;
  border-bottom: 1px solid var(--line);
}
.func-table td { padding: 6px; border-bottom: 1px dashed var(--line-soft); vertical-align: top; }
.func-table tr:last-child td { border-bottom: 0; }
.fq { color: var(--ink); }
.fh { color: var(--ink-2); }
.col-score { width: 120px; text-align: left; }
.col-score em { font-style: normal; font-size: 12px; color: var(--ink-3); }
.bar {
  display: inline-block;
  height: 4px;
  border-radius: 2px;
  background: var(--brand);
  opacity: 0.6;
  margin-right: 5px;
  vertical-align: middle;
}
.no-func { font-size: 12px; color: var(--ink-3); line-height: 1.8; }

.duo { display: flex; gap: 22px; flex-wrap: wrap; }
.duo > .block { flex: 1; min-width: 240px; }

.generic { display: flex; flex-wrap: wrap; gap: 6px; }
.generic-note { flex: 1 0 100%; font-size: 11.5px; color: var(--ink-3); margin: 6px 0 0; line-height: 1.7; }
.muted { font-size: 12px; color: var(--ink-3); }

.domain-row { display: flex; gap: 8px; font-size: 12.5px; padding: 2px 0; }
.dom-label { color: var(--ink-3); flex: 0 0 56px; }
.dom-val { color: var(--ink-2); }
.domain-note { font-size: 11.5px; color: var(--ink-3); margin: 6px 0 0; line-height: 1.7; }

/* 专家核定 */
.review-box {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  margin: 4px 0 12px;
  background: var(--panel);
}
.rv-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.rv-title { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.rv-hint { font-size: 12px; color: var(--ink-4); }

.rv-done { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rv-verdict {
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 5px;
  font-weight: 500;
}
.vf-duplicate { color: var(--danger); background: var(--danger-soft); }
.vf-not_duplicate { color: var(--success); background: var(--success-soft); }
.vf-need_more { color: var(--warn); background: var(--warn-bg); }
.rv-by { font-size: 11.5px; color: var(--ink-3); }
.rv-basis {
  margin: 8px 0 4px;
  font-size: 12.5px;
  color: var(--ink-2);
  line-height: 1.85;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--panel-2);
}

.rv-choices { margin-bottom: 8px; }
.rv-actions { margin-top: 8px; display: flex; gap: 8px; align-items: center; }
.rv-readonly { font-size: 12px; color: var(--ink-3); line-height: 1.85; }

/* 参考数据 */
.more { font-size: 12px; color: var(--brand); cursor: pointer; padding: 4px 0 2px; }
.more:hover { text-decoration: underline; }
.more-body { padding-top: 10px; }

.score-row {
  display: flex; justify-content: space-between;
  padding: 4px 0; font-size: 12px; color: var(--ink-2);
  border-bottom: 1px dashed var(--line-soft);
}
.score-row b { font-weight: 500; }
.score-row.total { border-bottom: 0; color: var(--ink); }
.score-note { font-size: 12px; color: var(--ink-4); margin: 6px 0 0; line-height: 1.7; }

.sec-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.sec-table td { padding: 3px 0; }
.sec-name { width: 96px; color: var(--ink-2); }
.sec-bar { width: 90px; }
.sec-bar span { display: block; height: 4px; border-radius: 2px; background: var(--brand); opacity: 0.5; }
.sec-val { width: 40px; text-align: right; color: var(--ink-3); font-size: 12px; }

.notes { font-size: 11.5px; color: var(--ink-3); line-height: 1.9; margin: 8px 0; }

.snip { border: 1px solid var(--line-soft); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.snip-head {
  display: flex; gap: 10px; align-items: center;
  padding: 6px 10px; background: var(--panel-2);
  font-size: 11.5px; color: var(--ink-3);
}
.snip-score { color: var(--brand); font-weight: 600; }
.snip-body { display: flex; }
.col { flex: 1; padding: 10px 12px; min-width: 0; }
.col + .col { border-left: 1px solid var(--line-soft); }
.col-tag { font-size: 12px; color: var(--ink-4); margin-bottom: 4px; }
.col p { margin: 0; font-size: 12.5px; line-height: 1.85; color: var(--ink-2); }

.foot-note {
  margin-top: 14px;
  font-size: 12px;
  color: var(--ink-4);
  line-height: 1.8;
}
</style>
