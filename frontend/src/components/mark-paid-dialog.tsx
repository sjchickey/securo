import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Check } from 'lucide-react'
import { transactions as transactionsApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'

type Props = {
  open: boolean
  onClose: () => void
  /** Ids being marked paid. All must be on `accountId`. */
  transactionIds: string[]
  /** The card these charges belong to — scopes the payment picker. */
  accountId: string | null
  onConfirm: (paidDate: string, coveredByPaymentId?: string) => void
  loading: boolean
}

const today = () => new Date().toISOString().slice(0, 10)

/**
 * Marks credit-card charges as paid, optionally recording which payment on the
 * same card settled them. The link is optional on purpose — "I paid this off"
 * is useful on its own, and forcing a payment pick would block users whose
 * payment transaction hasn't synced yet.
 */
export function MarkPaidDialog({
  open,
  onClose,
  transactionIds,
  accountId,
  onConfirm,
  loading,
}: Props) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()

  const [paymentId, setPaymentId] = useState('')
  const [paidDate, setPaidDate] = useState(today)

  // Reset when the dialog reopens against a different selection, using the
  // "adjust state on prop change" pattern the transfer dialog uses.
  const sessionKey = `${open ? '1' : '0'}-${accountId ?? ''}-${transactionIds.length}`
  const [prevSessionKey, setPrevSessionKey] = useState(sessionKey)
  if (sessionKey !== prevSessionKey) {
    setPrevSessionKey(sessionKey)
    setPaymentId('')
    setPaidDate(today())
  }

  // Payments on a card are credits: they reduce the balance owed.
  const { data: payments, isLoading: paymentsLoading } = useQuery({
    queryKey: ['card-payments', accountId],
    queryFn: () =>
      transactionsApi.list({
        account_id: accountId!,
        type: 'credit',
        limit: 25,
        sort_by: 'date',
        sort_dir: 'desc',
      }),
    enabled: open && !!accountId,
  })

  // A charge in the selection can't also be the payment that settled it.
  const candidates = (payments?.items ?? []).filter((p) => !transactionIds.includes(p.id))

  if (!open) return null

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden p-4 sm:max-w-lg sm:p-6">
        <DialogHeader>
          <DialogTitle>
            {t('transactions.markPaidTitle', { count: transactionIds.length })}
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 space-y-4 overflow-y-auto overscroll-contain pr-1">
          <DialogDescription>{t('transactions.markPaidDescription')}</DialogDescription>

          <div>
            <label
              htmlFor="mark-paid-date"
              className="mb-1.5 block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
            >
              {t('transactions.markPaidDateLabel')}
            </label>
            <input
              id="mark-paid-date"
              type="date"
              value={paidDate}
              onChange={(e) => setPaidDate(e.target.value)}
              className="w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30"
            />
          </div>

          <div>
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {t('transactions.markPaidPaymentLabel')}
            </p>
            {paymentsLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : candidates.length === 0 ? (
              <p className="py-4 text-center text-xs italic text-muted-foreground">
                {t('transactions.markPaidNoPaymentsFound')}
              </p>
            ) : (
              <ul className="-mx-1 max-h-64 space-y-1.5 overflow-y-auto px-1">
                <li>
                  <button
                    type="button"
                    onClick={() => setPaymentId('')}
                    className={`flex w-full items-center gap-2 rounded-lg border p-3 text-left text-sm transition-colors ${
                      paymentId === ''
                        ? 'border-primary/40 bg-primary/5'
                        : 'border-border bg-card hover:bg-muted/50'
                    }`}
                  >
                    {paymentId === '' && <Check size={14} className="shrink-0 text-primary" />}
                    <span className="text-muted-foreground">
                      {t('transactions.markPaidNoPayment')}
                    </span>
                  </button>
                </li>
                {candidates.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => setPaymentId(p.id)}
                      className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors ${
                        paymentId === p.id
                          ? 'border-primary/40 bg-primary/5'
                          : 'border-border bg-card hover:bg-muted/50'
                      }`}
                    >
                      {paymentId === p.id && <Check size={14} className="shrink-0 text-primary" />}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-foreground">
                          {p.description}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {new Date(p.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                        </p>
                      </div>
                      <p className="shrink-0 text-sm font-bold tabular-nums text-emerald-600">
                        +{formatCurrency(Math.abs(Number(p.amount)), p.currency, locale)}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <DialogFooter className="shrink-0 gap-2 sm:gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={loading}>
            {t('common.cancel')}
          </Button>
          <Button
            type="button"
            onClick={() => onConfirm(paidDate, paymentId || undefined)}
            disabled={loading}
          >
            {loading ? t('common.loading') : t('transactions.markPaid')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
