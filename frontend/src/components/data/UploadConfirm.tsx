import { ConfirmDialog } from '@/components/ConfirmDialog'
import type { UploadPreview } from '@/lib/types'

/* The step between choosing a file and losing what it replaces.

   An upload is a replace, and the replace cascades: exceptions derived from
   the outgoing rows go, and so do the actions attached to them. Some of those
   actions have a RazorpayX payout id, meaning money already moved -- deleting
   their record does not un-move it, it only removes this app's memory of it.
   That is the one number here worth its own sentence.

   Every count comes from POST …?dry_run=true, which measures the real rows
   rather than guessing from what the file looks like. */
export function UploadConfirm({
  title,
  preview,
  committing,
  onConfirm,
  onCancel,
}: {
  title: string
  preview: UploadPreview
  committing: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const { rows_to_load, rows_to_delete, exceptions_to_delete, actions_to_delete } = preview
  const live = preview.live_payouts_affected
  const plural = (n: number, word: string) => `${word}${n === 1 ? '' : 's'}`

  return (
    <ConfirmDialog
      title={`Replace the ${title.toLowerCase()}?`}
      titleId="upload-confirm-title"
      onConfirm={onConfirm}
      onCancel={onCancel}
      confirmDisabled={committing}
      confirmLabel={
        committing ? 'Replacing…' : `Replace with ${rows_to_load} ${plural(rows_to_load, 'row')}`
      }
    >
      <div className="modal-body">
        <p>
          This loads <b>{rows_to_load}</b> {plural(rows_to_load, 'row')} and deletes{' '}
          <b>{rows_to_delete}</b> existing {plural(rows_to_delete, 'row')}. It can’t be undone
          from here.
        </p>
        <ul className="modal-list">
          <li>
            <b>{exceptions_to_delete}</b> {plural(exceptions_to_delete, 'exception')} cleared —
            they describe rows that will no longer exist
          </li>
          <li>
            <b>{actions_to_delete}</b> {plural(actions_to_delete, 'action')} cleared — the
            attempts made against those exceptions
          </li>
        </ul>
        {live > 0 && (
          /* Deliberately one flat sentence rather than a bolded count inside
             a paragraph: this is the line that should be read whole. */
          <p className="modal-warn">
            {`${live} dispatched ${plural(live, 'payout')} already moved money. Replacing this
              data deletes the only record of ${live === 1 ? 'it' : 'them'} kept here — RazorpayX
              still has ${live === 1 ? 'its' : 'their'} own.`.replace(/\s+/g, ' ')}
          </p>
        )}
      </div>
    </ConfirmDialog>
  )
}
