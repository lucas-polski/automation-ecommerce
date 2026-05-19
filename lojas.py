"""
Cadastro das lojas Pophouse — pontos de partida/retorno da rota.

Cada loja tem endereço completo (string) + latitude e longitude.
Pra adicionar nova loja, basta acrescentar uma entrada nesse dicionário.

⚠️ As coordenadas devem ser PRECISAS (pega no Google Maps com botão direito).
Imprecisão aqui impacta a otimização da rota inteira pela Cobli.
"""

LOJAS = {
    "1": {
        "codigo": "centro",
        "nome": "Loja Centro",
        "endereco": "Rua Mariano Torres, 948, Centro, Curitiba - PR, 80060-120, Brasil",
        "latitude": -25.4307086,
        "longitude": -49.2623633,
    },
    "2": {
        "codigo": "juveve",
        "nome": "Loja Juvevê",
        "endereco": "Rua Padre Germano Mayer, 2070, Juvevê, Curitiba - PR, Brasil",
        # ⚠️ ESTIMATIVA — abre o Google Maps, busca "R. Padre Germano Mayer, 2070,
        # Curitiba", clica direito no pin e cola as coords exatas aqui.
        "latitude": -25.4220000,
        "longitude": -49.2480000,
    },
}