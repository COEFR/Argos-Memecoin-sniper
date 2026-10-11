# Argos — inteligencia on-chain de memecoins (Solana)

Detector de **grupos de wallets que compran juntas** en la blockchain de Solana, sin depender de grupos
de pago ni listas de KOLs. El sistema:

1. **cosecha** lanzamientos de pump.fun y lee quién compró cada uno (wallets, montos y a qué segundo);
2. **arma grupos** con las wallets que coinciden comprando en 2+ lanzamientos distintos (union-find);
3. **identifica el origen** de cada grupo, su perfil (personas o granjas de bots) y lo nombra;
4. **vigila en vivo** (WebSocket) las wallets que dan plata: avisa en **~0,7 s** desde la compra;
5. **mide** cada aviso (FDV al aviso → pico posterior) y **clasifica** los grupos por resultado real;
6. corre además un **test de control** (tokens al azar, medidos igual) para saber si el sistema le gana
   al azar en vez de creerle a la intuición.

> Solo informa: no opera ni mueve fondos. Los datos y las claves viven fuera del repositorio.
