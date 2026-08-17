import { describe, expect, it } from 'vitest'

import {
  findTransferCategoryId,
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

describe('findTransferCategoryId', () => {
  const flagged = (name: string, id = name) => ({ id, name, treat_as_transfer: true })

  it('picks the transfer category over other transfer-flagged ones', () => {
    // Workspaces typically flag Investments too, since a one-sided movement
    // into an asset is also excluded from P&L.
    expect(findTransferCategoryId([flagged('Investments'), flagged('Transfers')]))
      .toBe('Transfers')
  })

  it('is not order-dependent', () => {
    expect(findTransferCategoryId([flagged('Transfers'), flagged('Investments')]))
      .toBe('Transfers')
  })

  it('falls back to a name match when nothing is flagged', () => {
    expect(findTransferCategoryId([{ id: 'a', name: 'Groceries' }, { id: 'b', name: 'Transfers' }]))
      .toBe('b')
  })

  it('falls back to any flagged category when none is named for transfers', () => {
    expect(findTransferCategoryId([{ id: 'a', name: 'Groceries' }, flagged('Investments', 'b')]))
      .toBe('b')
  })

  it('returns empty when there is no sensible default', () => {
    expect(findTransferCategoryId([{ id: 'a', name: 'Groceries' }])).toBe('')
    expect(findTransferCategoryId([])).toBe('')
  })
})
