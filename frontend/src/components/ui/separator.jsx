import { cn } from "@/lib/utils";

function Separator({ className, orientation = "horizontal", dashed = false, ...props }) {
  return (
    <div role="separator" aria-orientation={orientation} data-slot="separator"
      className={cn(
        "shrink-0",
        orientation === "horizontal" ? "w-full" : "h-full",
        dashed
          ? orientation === "horizontal" ? "border-t border-dashed" : "border-l border-dashed"
          : orientation === "horizontal" ? "h-px bg-border" : "w-px bg-border",
        className
      )}
      {...props} />
  );
}

export { Separator };
