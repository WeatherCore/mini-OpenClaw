import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:opacity-50 gap-1.5',
  {
    variants: {
      variant: {
        default: 'bg-accent text-white hover:bg-accent-emphasis',
        outline: 'border border-border-default bg-canvas-default hover:bg-canvas-subtle text-fg-default',
        ghost: 'hover:bg-canvas-subtle text-fg-default',
        subtle: 'bg-canvas-subtle text-fg-default hover:bg-canvas-inset',
        danger: 'bg-danger text-white hover:opacity-90',
        link: 'text-accent hover:underline underline-offset-2',
      },
      size: {
        default: 'h-8 px-3',
        sm: 'h-7 px-2 text-xs',
        icon: 'h-8 w-8',
        'icon-sm': 'h-7 w-7',
      },
    },
    defaultVariants: { variant: 'ghost', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp className={cn(buttonVariants({ variant, size }), className)} ref={ref} {...props} />
  },
)
Button.displayName = 'Button'
export { buttonVariants }
