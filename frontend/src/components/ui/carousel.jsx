import * as React from "react";
import useEmblaCarousel from "embla-carousel-react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

// Carrusel de shadcn/ui sobre Embla. Embla mueve las diapositivas con style.transform desde JS (CSSOM),
// que la CSP permite: no inyecta <style>.
const CarouselContext = React.createContext(null);

function useCarousel() {
  const context = React.useContext(CarouselContext);
  if (!context) throw new Error("useCarousel debe usarse dentro de <Carousel />");
  return context;
}

function Carousel({ opts, plugins, className, children, ...props }) {
  const [carouselRef, api] = useEmblaCarousel(opts, plugins);
  const [canScrollPrev, setCanScrollPrev] = React.useState(false);
  const [canScrollNext, setCanScrollNext] = React.useState(false);

  const onSelect = React.useCallback((embla) => {
    if (!embla) return;
    setCanScrollPrev(embla.canScrollPrev());
    setCanScrollNext(embla.canScrollNext());
  }, []);

  React.useEffect(() => {
    if (!api) return undefined;
    onSelect(api);
    api.on("reInit", onSelect);
    api.on("select", onSelect);
    return () => { api.off("reInit", onSelect); api.off("select", onSelect); };
  }, [api, onSelect]);

  const onKeyDown = React.useCallback((event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); api?.scrollPrev(); }
    if (event.key === "ArrowRight") { event.preventDefault(); api?.scrollNext(); }
  }, [api]);

  return (
    <CarouselContext.Provider value={{ carouselRef, api, canScrollPrev, canScrollNext,
      scrollPrev: () => api?.scrollPrev(), scrollNext: () => api?.scrollNext() }}>
      <div onKeyDownCapture={onKeyDown} className={cn("relative", className)}
        role="region" aria-roledescription="carousel" data-slot="carousel" {...props}>
        {children}
      </div>
    </CarouselContext.Provider>
  );
}

function CarouselContent({ className, ...props }) {
  const { carouselRef } = useCarousel();
  return (
    <div ref={carouselRef} className="overflow-hidden" data-slot="carousel-content">
      <div className={cn("-ml-4 flex", className)} {...props} />
    </div>
  );
}

function CarouselItem({ className, ...props }) {
  return (
    <div role="group" aria-roledescription="slide" data-slot="carousel-item"
      className={cn("min-w-0 shrink-0 grow-0 basis-full pl-4", className)} {...props} />
  );
}

function CarouselPrevious({ className, ...props }) {
  const { scrollPrev, canScrollPrev } = useCarousel();
  return (
    <Button variant="outline" size="icon" className={cn("rounded-full", className)}
      disabled={!canScrollPrev} onClick={scrollPrev} aria-label="Anterior" {...props}>
      <ArrowLeft />
    </Button>
  );
}

function CarouselNext({ className, ...props }) {
  const { scrollNext, canScrollNext } = useCarousel();
  return (
    <Button variant="outline" size="icon" className={cn("rounded-full", className)}
      disabled={!canScrollNext} onClick={scrollNext} aria-label="Siguiente" {...props}>
      <ArrowRight />
    </Button>
  );
}

export { Carousel, CarouselContent, CarouselItem, CarouselPrevious, CarouselNext };
