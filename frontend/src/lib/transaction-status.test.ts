import { describe, expect, it } from 'vitest'

import {
  getPaidRowClassName,
  isTransactionPaid,
  shouldShowPendingBadge,
} from './transaction-status'

describe('shouldShowPendingBadge', () => {
  it('shows pending for transactions managed by Securo', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'manual' })).toBe(true)
    expect(shouldShowPendingBadge({ status: 'pending', source: 'recurring' })).toBe(true)
  })

  it('hides pending for bank-synced transactions', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'sync' })).toBe(false)
  })

  it('hides the badge for posted transactions', () => {
    expect(shouldShowPendingBadge({ status: 'posted', source: 'manual' })).toBe(false)
  })
})

describe('isTransactionPaid', () => {
  it('reports the paid flag', () => {
    expect(isTransactionPaid({ is_paid: true })).toBe(true)
    expect(isTransactionPaid({ is_paid: false })).toBe(false)
  })
})

describe('getPaidRowClassName', () => {
  it('styles paid rows and leaves unpaid rows untouched', () => {
    expect(getPaidRowClassName({ is_paid: true })).toBe('transaction-row-paid')
    expect(getPaidRowClassName({ is_paid: false })).toBe('')
  })
})
