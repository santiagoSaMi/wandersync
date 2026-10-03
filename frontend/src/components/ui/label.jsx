import * as React from "react";
import { cn } from "@/lib/utils";

function Label({ className, ...props }) {
  return (
    <label data-slot="label"
      className={cn("text-[13px] font-medium leading-none text-muted-foreground select-none", className)} {...props} />
  );
}

export { Label };
