import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";

// Toda la comunicación frontend <-> backend pasa por el API Gateway GraphQL (/graphql).
// credentials: "same-origin" => el navegador envía la cookie de sesión HttpOnly (que JS nunca ve).
// Los pasos de la SAGA y los ítems no tienen id: cada respuesta de una orden trae la lista COMPLETA y
// vigente, así que se reemplaza (sin esto Apollo advierte de posible pérdida de datos en cada sondeo).
export function createCache() {
  return new InMemoryCache({
    typePolicies: { Order: { fields: { steps: { merge: false }, items: { merge: false } } } },
  });
}

export const client = new ApolloClient({
  link: new HttpLink({ uri: "/graphql", credentials: "same-origin" }),
  cache: createCache(),
});
