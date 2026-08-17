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

/** The category to default a card payment to.
 *
 * `counts_as_pnl` on the backend excludes anything in a treat-as-transfer
 * category, which is what stops a payment netting against the charges it
 * settles — so the default has to carry that flag. But the flag covers
 * one-sided movements generally, including investment applications, so a
 * workspace usually has several. Name decides between them; the flag is the
 * tiebreak, and a bare name match is the last resort for workspaces that
 * never set it.
 */
export function findTransferCategoryId(
  categories: { id: string; name: string; treat_as_transfer?: boolean }[],
): string {
  const named = (c: { name: string }) => /transfer/i.test(c.name)
  return (
    categories.find(c => c.treat_as_transfer && named(c))
    ?? categories.find(c => named(c))
    ?? categories.find(c => c.treat_as_transfer)
  )?.id ?? ''
}
