import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const field =
  "h-11 w-full min-w-0 rounded-lg border border-input bg-card px-3.5 text-[15px] shadow-xs transition-[color,box-shadow,border-color] outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-4 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50";

const Input = React.forwardRef(({ className, ...props }, ref) => (
  <input ref={ref} data-slot="input" className={cn(field, className)} {...props} />
));
Input.displayName = "Input";

// <select> nativo con estilo shadcn: accesible, sin portales ni estilos inyectados (compatible con la CSP).
const NativeSelect = React.forwardRef(({ className, children, icon: Icon, ...props }, ref) => (
  <div className="relative">
    {Icon && <Icon className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />}
    <select ref={ref} data-slot="native-select"
      className={cn(field, "appearance-none pr-10", Icon && "pl-10", className)} {...props}>
      {children}
    </select>
    <ChevronDown className="pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
  </div>
));
NativeSelect.displayName = "NativeSelect";

export { Input, NativeSelect };
