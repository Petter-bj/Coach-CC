-- Ukecoachen kan nå foreslå flere Hevy-maler i ett svar, hver knyttet til en
-- konkret dag i den valgte uken. Hver mal er fortsatt sin egen rad (én
-- pending/applied-status per mal), så «flere forslag» krevde ingen ny tabell.
--
-- Denne migreringen legger bare til to valgfrie kolonner for planleggings-
-- metadata. Eksisterende rader beholder NULL og forblir gyldige: den gamle
-- enkelt-rutine-modellen er dermed forlengs kompatibel. `routine_json` fortsetter
-- å inneholde bare det Hevy faktisk trenger (title, notes, exercises), mens dato
-- og hensikt er dashboard-metadata som aldri sendes til Hevy.
ALTER TABLE hevy_routine_proposals ADD COLUMN suggested_date TEXT;
ALTER TABLE hevy_routine_proposals ADD COLUMN purpose TEXT;
