-- En blokk eier styrkestrukturen: frekvens og stabile maler, men ikke en
-- låst liste med øvelser. Det gjør at ukecoachen kan holde programmet
-- konsistent uten å gjette på nye økter hver uke.
ALTER TABLE training_blocks ADD COLUMN strength_structure_json TEXT;
