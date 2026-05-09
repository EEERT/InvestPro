export interface Bond {
  code: string
  name: string
  price: number | null
  change_pct: number | null
  issue_size: number | null
  remaining_size: number | null
  stock_code: string | null
  stock_name: string | null
  stock_price: number | null
  stock_change_pct: number | null
  conv_price: number | null
  conv_value: number | null
  premium_rate: number | null
  bond_ratio: number | null
  expire_date: string | null
  updated_at: string
}

export interface BondListResponse {
  total: number
  items: Bond[]
}

export interface RefreshStatus {
  status: 'running' | 'success' | 'error' | 'never'
  started_at: string | null
  finished_at: string | null
  message: string | null
  cooldown_remaining: number
}

export interface RefreshResponse {
  accepted: boolean
  detail: string
}

export interface BondQueryParams {
  search?: string
  min_price?: number
  max_price?: number
  min_change?: number
  max_change?: number
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
}
