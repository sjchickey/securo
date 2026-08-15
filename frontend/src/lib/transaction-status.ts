type PendingBadgeTransaction = {
  source: string | null
  status: string | null
}

type PaidStatusTransaction = {
  is_paid: boolean
}

/** Bank-sync pending state already matches the provider and needs no UI badge. */
export function shouldShowPendingBadge(transaction: PendingBadgeTransaction): boolean {
  return transaction.status === 'pending' && transaction.source !== 'sync'
}

/** Check if a transaction is marked as paid. */
export function isTransactionPaid(transaction: PaidStatusTransaction): boolean {
  return transaction.is_paid === true
}

/** Get CSS class name for transaction row styling based on paid status. */
export function getPaidRowClassName(transaction: PaidStatusTransaction): string {
  return isTransactionPaid(transaction) ? 'transaction-row-paid' : ''
}
