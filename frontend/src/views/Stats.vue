<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { projectApi } from '@/api'
import { showError } from '@/api/http'
import { downloadBlob, blobErrorDetail } from '@/utils/fileview'

const loading = ref(false)
const exporting = ref('')
const data = ref(null)
const options = ref({ units: [], categories: [], years: [] })
const filters = reactive({ year: '', unit: '', category: '' })

const pct = (v) => `${Math.round((v || 0) * 100)}%`
const day = (v) => (v === null || v === undefined ? '—' : `${v} 天`)
const money = (v) =>
  (v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })

async function load() {
  loading.value = true
  try {
    const params = {
      year: filters.year || undefined,
      unit: filters.unit || undefined,
      category: filters.category || undefined,
    }
    const { data: d } = await projectApi.report(params)
    data.value = d
  } catch (e) {
    showError(e?.response?.data?.detail || '统计加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    const { data: d } = await projectApi.filters()
    options.value = d
  } catch { /* 下拉失败不影响统计本身 */ }
}

function reset() {
  filters.year = ''
  filters.unit = ''
  filters.category = ''
  load()
}

// 漏斗条最大宽度按第一阶段算，后面的阶段自然收窄，能看出流失
const funnelMax = computed(() => {
  const f = data.value?.funnel || []
  return Math.max(1, ...f.map((x) => x.count))
})

async function download(kind) {
  exporting.value = kind
  try {
    const params = {
      kind,
      year: filters.year || undefined,
      unit: filters.unit || undefined,
      category: filters.category || undefined,
    }
    const { data: blob, headers } = await projectApi.exportCsv(params)
    const cd = headers['content-disposition'] || ''
    let name = kind === 'events' ? '项目流转明细.csv' : '项目台账.csv'
    const m = /filename\*=UTF-8''([^;]+)/.exec(cd)
    if (m) name = decodeURIComponent(m[1])
    // 原来 a.click() 后立即 revokeObjectURL，Firefox/Safari 可能直接取消下载；
    // 复用项目里的 downloadBlob（延迟 10 秒回收 blob url）
    downloadBlob(new Blob([blob], { type: 'text/csv;charset=utf-8' }), name)
    ElMessage.success(`已导出 ${name}`)
  } catch (e) {
    // 导出失败时错误体也是 blob，拦截器读不到 detail，用工具函数读回来
    showError(await blobErrorDetail(e, '导出失败，请稍后重试'))
  } finally {
    exporting.value = ''
  }
}

onMounted(async () => {
  await Promise.all([loadOptions(), load()])
})
</script>

<template>
  <div v-loading="loading" class="stats-page">
    <div class="toolbar">
      <el-select v-model="filters.year" placeholder="全部年度" clearable
        class="pick" @change="load">
        <el-option v-for="y in options.years" :key="y" :label="`${y} 年度`" :value="y" />
      </el-select>
      <el-select v-model="filters.unit" placeholder="全部单位" clearable filterable
        class="pick-wide" @change="load">
        <el-option v-for="u in options.units" :key="u" :label="u" :value="u" />
      </el-select>
      <el-select v-model="filters.category" placeholder="全部领域" clearable
        class="pick" @change="load">
        <el-option v-for="c in options.categories" :key="c" :label="c" :value="c" />
      </el-select>
      <el-button link @click="reset">重置</el-button>
      <div class="spacer" />
      <el-button :loading="exporting === 'projects'" @click="download('projects')">
        导出台账
      </el-button>
      <el-button :loading="exporting === 'events'" @click="download('events')">
        导出流转明细
      </el-button>
    </div>

    <template v-if="data">
      <div class="stat-strip five">
        <div class="stat-tile">
          <div class="stat-num">{{ data.summary.total }}</div>
          <div class="stat-label">项目总数</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ data.summary.in_progress }}</div>
          <div class="stat-label">在办</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ data.summary.accepted }}</div>
          <div class="stat-label">已验收</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ money(data.summary.investment_total) }}</div>
          <div class="stat-label">在库投资（万元）</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">
            {{ data.summary.avg_cycle_days === null ? '—' : data.summary.avg_cycle_days }}
          </div>
          <div class="stat-label">平均建设周期（天）· {{ data.summary.cycle_samples }} 个样本</div>
        </div>
      </div>

      <div class="row2">
        <el-card shadow="never" class="card">
          <template #header>
            <div class="card-head">
              <span class="card-title">阶段停留</span>
              <span class="card-sub">按流转记录切段，已结束的阶段才算均值</span>
            </div>
          </template>
          <el-table :data="data.stage_days" size="small">
            <el-table-column prop="label" label="阶段" width="96" />
            <el-table-column label="平均停留" width="92" align="right">
              <template #default="{ row }">{{ day(row.avg_days) }}</template>
            </el-table-column>
            <el-table-column label="样本" width="62" align="right">
              <template #default="{ row }">{{ row.finished }}</template>
            </el-table-column>
            <el-table-column label="当前停留" align="right">
              <template #default="{ row }">
                <span v-if="!row.stuck" class="muted">—</span>
                <span v-else :class="['muted', { slow: row.stuck_max_days >= 30 }]">
                  {{ row.stuck }} 个，最长 {{ day(row.stuck_max_days) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
          <div class="note">在办项目单列「当前停留」，不并入均值。</div>
        </el-card>

        <el-card shadow="never" class="card">
          <template #header>
            <div class="card-head">
              <span class="card-title">流转漏斗</span>
              <span class="card-sub">进入过该阶段的项目数</span>
            </div>
          </template>
          <div class="funnel">
            <div v-for="f in data.funnel" :key="f.key" class="funnel-row">
              <span class="funnel-label">{{ f.label }}</span>
              <div class="funnel-track">
                <div class="funnel-bar" :style="{ width: (f.count / funnelMax) * 100 + '%' }" />
              </div>
              <span class="funnel-n">{{ f.count }}</span>
            </div>
          </div>
          <div class="kpis">
            <div class="kpi">
              <div class="kpi-n">{{ pct(data.return_rate.rate) }}</div>
              <div class="kpi-l">
                退回率（{{ data.return_rate.returned }}/{{ data.return_rate.submitted }}）
              </div>
            </div>
            <div class="kpi">
              <div class="kpi-n">{{ pct(data.review.rate) }}</div>
              <div class="kpi-l">评审通过率（{{ data.review.concluded }} 个已出结论）</div>
            </div>
          </div>
        </el-card>
      </div>

      <div class="row2">
        <el-card shadow="never" class="card">
          <template #header>
            <div class="card-head">
              <span class="card-title">分单位</span>
              <span class="card-sub">按投资额排序</span>
            </div>
          </template>
          <el-table :data="data.by_unit" size="small" :show-header="true">
            <el-table-column prop="name" label="申报单位" show-overflow-tooltip />
            <el-table-column prop="total" label="项目" width="60" align="right" />
            <el-table-column label="投资(万元)" width="106" align="right">
              <template #default="{ row }">{{ money(row.investment) }}</template>
            </el-table-column>
            <el-table-column prop="accepted" label="已验收" width="72" align="right" />
          </el-table>
        </el-card>

        <el-card shadow="never" class="card">
          <template #header>
            <div class="card-head">
              <span class="card-title">分领域 / 分年度</span>
              <span class="card-sub">同一批数据两种切法</span>
            </div>
          </template>
          <el-table :data="data.by_category" size="small">
            <el-table-column prop="name" label="领域" />
            <el-table-column prop="total" label="项目" width="60" align="right" />
            <el-table-column label="投资(万元)" width="106" align="right">
              <template #default="{ row }">{{ money(row.investment) }}</template>
            </el-table-column>
          </el-table>
          <el-table :data="data.by_year" size="small" class="mt8">
            <el-table-column prop="name" label="年度" />
            <el-table-column prop="total" label="项目" width="60" align="right" />
            <el-table-column label="投资(万元)" width="106" align="right">
              <template #default="{ row }">{{ money(row.investment) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <div class="foot">
        统计口径：阶段耗时按流转记录切段，同一阶段停留多次则累加，分母是走过该阶段的项目数；
        退回率＝进过「待补正」的项目数 ÷ 提交过的项目数；评审通过率分母是已出评审结论的项目。
        数据截至 {{ data.generated_at }}。
      </div>
    </template>

    <el-card v-else-if="!loading" shadow="never" class="card">
      <div class="empty">还没有可统计的数据。等有项目提交后这里会自动出数。</div>
    </el-card>
  </div>
</template>

<style scoped>
.stats-page { max-width: 1480px; }

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 16px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
}
.spacer { flex: 1; }
.pick { width: 124px; }
.pick-wide { width: 196px; }

.stat-strip.five { grid-template-columns: repeat(5, minmax(0, 1fr)); }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
@media (max-width: 1180px) {
  .row2 { grid-template-columns: 1fr; }
  .stat-strip.five { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

.card { background: var(--panel); }
.card-head { display: flex; align-items: center; gap: 10px; }
.card-title { font-size: 15px; font-weight: 600; color: var(--ink); letter-spacing: -0.1px; }
.card-sub { font-size: 11.5px; color: var(--ink-3); }

.note { font-size: 11.5px; color: var(--ink-4); line-height: 1.8; margin-top: 12px; }

.funnel { display: flex; flex-direction: column; gap: 6px; }
.funnel-row { display: grid; grid-template-columns: 70px 1fr 28px; align-items: center; gap: 10px; }
.funnel-label { font-size: 12px; color: var(--ink-3); }
.funnel-track { height: 12px; background: var(--panel-3); border-radius: 6px; overflow: hidden; }
.funnel-bar {
  height: 100%;
  background: linear-gradient(90deg, #86B6EF, #2A78D6);
  border-radius: 6px;
  transition: width .3s;
}
.funnel-n {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-2);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.kpis { display: flex; gap: 12px; margin-top: 16px; }
.kpi {
  flex: 1;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  padding: 11px 14px;
}
.kpi-n { font-size: 22px; font-weight: 600; color: var(--ink); letter-spacing: -0.4px; font-variant-numeric: tabular-nums; }
.kpi-l { font-size: 12px; color: var(--ink-3); line-height: 1.6; margin-top: 2px; }

.muted { color: var(--ink-3); font-size: 12px; }
.muted.slow { color: var(--warn); }
.mt8 { margin-top: 8px; }
.empty { padding: 26px 0; color: var(--ink-3); font-size: 12.5px; }
.foot { font-size: 11.5px; color: var(--ink-4); line-height: 1.9; }
</style>
