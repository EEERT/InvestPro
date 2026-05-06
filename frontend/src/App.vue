<template>
  <div class="page">
    <!-- Header -->
    <div class="page-header">
      <h1 class="title">📈 可转债数据查看器</h1>
      <div class="header-actions">
        <el-tag v-if="refreshStatus" :type="statusTagType" class="status-tag">
          {{ statusLabel }}
        </el-tag>
        <el-tooltip :content="refreshTipText" placement="bottom">
          <el-button
            type="primary"
            :loading="isRefreshing"
            :disabled="cooldownRemaining > 0 || isRefreshing"
            @click="handleRefresh"
          >
            <el-icon v-if="!isRefreshing"><Refresh /></el-icon>
            {{ isRefreshing ? '刷新中...' : cooldownRemaining > 0 ? `等待 ${cooldownRemaining}s` : '刷新数据' }}
          </el-button>
        </el-tooltip>
      </div>
    </div>

    <!-- Last update info -->
    <div v-if="refreshStatus?.finished_at" class="update-info">
      最后更新：{{ formatTime(refreshStatus.finished_at) }}
      <span v-if="refreshStatus.message" class="update-msg"> · {{ refreshStatus.message }}</span>
    </div>

    <!-- Filters -->
    <el-card class="filter-card" shadow="never">
      <el-row :gutter="12" align="middle">
        <el-col :xs="24" :sm="8" :md="6">
          <el-input
            v-model="search"
            placeholder="搜索代码/名称/正股"
            clearable
            @input="debouncedLoad"
            @clear="loadBonds"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-col>
        <el-col :xs="12" :sm="6" :md="4">
          <el-input-number
            v-model="minPrice"
            :min="0"
            :precision="2"
            placeholder="最低价"
            controls-position="right"
            style="width:100%"
            @change="loadBonds"
          />
        </el-col>
        <el-col :xs="12" :sm="6" :md="4">
          <el-input-number
            v-model="maxPrice"
            :min="0"
            :precision="2"
            placeholder="最高价"
            controls-position="right"
            style="width:100%"
            @change="loadBonds"
          />
        </el-col>
        <el-col :xs="12" :sm="6" :md="4">
          <el-input-number
            v-model="minChange"
            :precision="2"
            placeholder="最低涨跌幅%"
            controls-position="right"
            style="width:100%"
            @change="loadBonds"
          />
        </el-col>
        <el-col :xs="12" :sm="6" :md="4">
          <el-input-number
            v-model="maxChange"
            :precision="2"
            placeholder="最高涨跌幅%"
            controls-position="right"
            style="width:100%"
            @change="loadBonds"
          />
        </el-col>
        <el-col :xs="24" :sm="4" :md="2">
          <el-button @click="resetFilters">重置</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- Table -->
    <el-card class="table-card" shadow="never">
      <el-table
        v-loading="loading"
        :data="bonds"
        stripe
        border
        style="width:100%"
        @sort-change="handleSortChange"
        :default-sort="{ prop: 'code', order: 'ascending' }"
      >
        <el-table-column prop="code" label="转债代码" width="110" sortable="custom" fixed />
        <el-table-column prop="name" label="转债名称" min-width="120" sortable="custom" />
        <el-table-column prop="price" label="最新价" width="100" sortable="custom" align="right">
          <template #default="{ row }">
            <span>{{ fmt2(row.price) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="change_pct" label="涨跌幅" width="100" sortable="custom" align="right">
          <template #default="{ row }">
            <span :class="changeCls(row.change_pct)">
              {{ fmtChange(row.change_pct) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="issue_size" label="发行量(亿)" width="110" sortable="custom" align="right">
          <template #default="{ row }">{{ fmt2(row.issue_size) }}</template>
        </el-table-column>
        <el-table-column prop="stock_code" label="正股代码" width="100" sortable="custom" />
        <el-table-column prop="stock_name" label="正股名称" min-width="110" sortable="custom" />
        <el-table-column prop="stock_price" label="正股价" width="90" sortable="custom" align="right">
          <template #default="{ row }">{{ fmt2(row.stock_price) }}</template>
        </el-table-column>
        <el-table-column prop="stock_change_pct" label="正股涨跌" width="100" sortable="custom" align="right">
          <template #default="{ row }">
            <span :class="changeCls(row.stock_change_pct)">
              {{ fmtChange(row.stock_change_pct) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="conv_price" label="转股价" width="90" sortable="custom" align="right">
          <template #default="{ row }">{{ fmt2(row.conv_price) }}</template>
        </el-table-column>
        <el-table-column prop="conv_value" label="转股价值" width="100" sortable="custom" align="right">
          <template #default="{ row }">{{ fmt2(row.conv_value) }}</template>
        </el-table-column>
        <el-table-column prop="premium_rate" label="溢价率%" width="100" sortable="custom" align="right">
          <template #default="{ row }">
            <span :class="changeCls(row.premium_rate)">
              {{ fmtChange(row.premium_rate) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="expire_date" label="到期时间" width="110" sortable="custom" align="center">
          <template #default="{ row }">{{ row.expire_date ?? '-' }}</template>
        </el-table-column>
      </el-table>

      <div class="table-footer">
        共 <strong>{{ total }}</strong> 条记录
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { fetchBonds, triggerRefresh, fetchRefreshStatus } from './api/bonds'
import type { Bond, RefreshStatus } from './api/types'

// ── state ──────────────────────────────────────────────────────────────────────
const bonds = ref<Bond[]>([])
const total = ref(0)
const loading = ref(false)
const isRefreshing = ref(false)
const cooldownRemaining = ref(0)
const refreshStatus = ref<RefreshStatus | null>(null)

// filters
const search = ref('')
const minPrice = ref<number | undefined>(undefined)
const maxPrice = ref<number | undefined>(undefined)
const minChange = ref<number | undefined>(undefined)
const maxChange = ref<number | undefined>(undefined)

// sort
const sortBy = ref('code')
const sortDir = ref<'asc' | 'desc'>('asc')

// ── computed ───────────────────────────────────────────────────────────────────
const statusTagType = computed(() => {
  const s = refreshStatus.value?.status
  if (s === 'success') return 'success'
  if (s === 'error') return 'danger'
  if (s === 'running') return 'warning'
  return 'info'
})

const statusLabel = computed(() => {
  const s = refreshStatus.value?.status
  if (s === 'success') return '数据正常'
  if (s === 'error') return '上次刷新失败'
  if (s === 'running') return '刷新中'
  if (s === 'never') return '暂无数据'
  return ''
})

const refreshTipText = computed(() => {
  if (isRefreshing.value) return '正在刷新数据，请稍候'
  if (cooldownRemaining.value > 0) return `刷新冷却中，请等待 ${cooldownRemaining.value} 秒`
  return '点击从 AkShare 拉取最新可转债数据'
})

// ── helpers ────────────────────────────────────────────────────────────────────
function fmt2(v: number | null | undefined): string {
  if (v == null) return '-'
  return v.toFixed(2)
}

function fmtChange(v: number | null | undefined): string {
  if (v == null) return '-'
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function changeCls(v: number | null | undefined): string {
  if (v == null) return ''
  return v > 0 ? 'text-rise' : v < 0 ? 'text-fall' : ''
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

// ── debounce ───────────────────────────────────────────────────────────────────
let debounceTimer: ReturnType<typeof setTimeout> | null = null
function debouncedLoad() {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => loadBonds(), 400)
}

// ── data loading ───────────────────────────────────────────────────────────────
async function loadBonds() {
  loading.value = true
  try {
    const res = await fetchBonds({
      search: search.value || undefined,
      min_price: minPrice.value,
      max_price: maxPrice.value,
      min_change: minChange.value,
      max_change: maxChange.value,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
    })
    bonds.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error('加载数据失败，请检查后端服务是否运行')
  } finally {
    loading.value = false
  }
}

async function loadRefreshStatus() {
  try {
    const s = await fetchRefreshStatus()
    refreshStatus.value = s
    isRefreshing.value = s.status === 'running'
    cooldownRemaining.value = s.cooldown_remaining
  } catch {
    // ignore
  }
}

// ── actions ────────────────────────────────────────────────────────────────────
async function handleRefresh() {
  try {
    const res = await triggerRefresh()
    if (res.accepted) {
      isRefreshing.value = true
      ElMessage.info('刷新已开始，稍后自动更新列表')
      pollRefreshStatus()
    } else {
      ElMessage.warning(res.detail)
    }
  } catch {
    ElMessage.error('触发刷新失败')
  }
}

function resetFilters() {
  search.value = ''
  minPrice.value = undefined
  maxPrice.value = undefined
  minChange.value = undefined
  maxChange.value = undefined
  loadBonds()
}

function handleSortChange({ prop, order }: { prop: string; order: string | null }) {
  sortBy.value = prop || 'code'
  sortDir.value = order === 'descending' ? 'desc' : 'asc'
  loadBonds()
}

// ── polling ────────────────────────────────────────────────────────────────────
let pollTimer: ReturnType<typeof setTimeout> | null = null

function pollRefreshStatus() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(async () => {
    await loadRefreshStatus()
    if (isRefreshing.value) {
      pollRefreshStatus()
    } else {
      // Refresh finished: reload the list
      await loadBonds()
    }
  }, 2000)
}

// Periodic cooldown countdown
let cdTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await loadRefreshStatus()
  await loadBonds()

  // Countdown timer
  cdTimer = setInterval(() => {
    if (cooldownRemaining.value > 0) {
      cooldownRemaining.value = Math.max(0, cooldownRemaining.value - 1)
    }
  }, 1000)
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
  if (debounceTimer) clearTimeout(debounceTimer)
  if (cdTimer) clearInterval(cdTimer)
})
</script>

<style scoped>
.page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 20px 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 12px;
}

.title {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-tag {
  font-size: 13px;
}

.update-info {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
}

.update-msg {
  color: #606266;
}

.filter-card {
  margin-bottom: 16px;
}

.table-card {
  margin-bottom: 20px;
}

.table-footer {
  margin-top: 14px;
  text-align: right;
  font-size: 13px;
  color: #909399;
}

.text-rise {
  color: #f56c6c;
  font-weight: 600;
}

.text-fall {
  color: #67c23a;
  font-weight: 600;
}
</style>
