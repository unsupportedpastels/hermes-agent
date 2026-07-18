import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent, DialogTitle } from './dialog'

function ControlledDialog({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const [open, setOpen] = useState(true)

  return (
    <Dialog
      onOpenChange={next => {
        setOpen(next)
        onOpenChange(next)
      }}
      open={open}
    >
      <DialogContent>
        <DialogTitle>Update available</DialogTitle>
      </DialogContent>
    </Dialog>
  )
}

describe('DialogContent close button', () => {
  it('closes a controlled dialog when clicked', () => {
    const onOpenChange = vi.fn()
    render(<ControlledDialog onOpenChange={onOpenChange} />)

    fireEvent.click(screen.getByRole('button', { name: /close/i }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
