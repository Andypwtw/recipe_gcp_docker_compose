const dbName = process.env.MONGO_INITDB_DATABASE || "recipe_ai";
const appDb = db.getSiblingDB(dbName);

if (!appDb.getCollectionNames().includes("raw_recipes")) {
  appDb.createCollection("raw_recipes");
}
appDb.raw_recipes.createIndex({SEQ: 1}, {unique: true});
appDb.raw_recipes.createIndex({prefix: 1, seq_num: 1});
