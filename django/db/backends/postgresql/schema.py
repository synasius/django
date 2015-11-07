import psycopg2

from django.db.backends.base.schema import BaseDatabaseSchemaEditor


class DatabaseSchemaEditor(BaseDatabaseSchemaEditor):

    sql_alter_column_type = "ALTER COLUMN %(column)s TYPE %(type)s USING %(column)s::%(type)s"

    sql_create_sequence = "CREATE SEQUENCE %(sequence)s"
    sql_delete_sequence = "DROP SEQUENCE IF EXISTS %(sequence)s CASCADE"
    sql_set_sequence_max = "SELECT setval('%(sequence)s', MAX(%(column)s)) FROM %(table)s"

    sql_create_varchar_index = "CREATE INDEX %(name)s ON %(table)s (%(columns)s varchar_pattern_ops)%(extra)s"
    sql_create_text_index = "CREATE INDEX %(name)s ON %(table)s (%(columns)s text_pattern_ops)%(extra)s"

    def quote_value(self, value):
        return psycopg2.extensions.adapt(value)

    def _model_indexes_sql(self, model):
        output = super(DatabaseSchemaEditor, self)._model_indexes_sql(model)
        if not model._meta.managed or model._meta.proxy or model._meta.swapped:
            return output

        for field in model._meta.local_fields:
            db_type = field.db_type(connection=self.connection)
            if db_type is not None and (field.db_index or field.unique):
                # Fields with database column types of `varchar` and `text` need
                # a second index that specifies their operator class, which is
                # needed when performing correct LIKE queries outside the
                # C locale. See #12234.
                #
                # The same doesn't apply to array fields such as varchar[size]
                # and text[size], so skip them.
                if '[' in db_type:
                    continue
                if db_type.startswith('varchar'):
                    output.append(self._create_index_sql(
                        model, [field], suffix='_like', sql=self.sql_create_varchar_index))
                elif db_type.startswith('text'):
                    output.append(self._create_index_sql(
                        model, [field], suffix='_like', sql=self.sql_create_text_index))
        return output

    def _alter_column_type_sql(self, table, old_field, new_field, new_type):
        """
        Makes ALTER TYPE with SERIAL make sense.
        """
        if new_type.lower() == "serial":
            column = new_field.column
            sequence_name = "%s_%s_seq" % (table, column)
            return (
                (
                    self.sql_alter_column_type % {
                        "column": self.quote_name(column),
                        "type": "integer",
                    },
                    [],
                ),
                [
                    (
                        self.sql_delete_sequence % {
                            "sequence": self.quote_name(sequence_name),
                        },
                        [],
                    ),
                    (
                        self.sql_create_sequence % {
                            "sequence": self.quote_name(sequence_name),
                        },
                        [],
                    ),
                    (
                        self.sql_alter_column % {
                            "table": self.quote_name(table),
                            "changes": self.sql_alter_column_default % {
                                "column": self.quote_name(column),
                                "default": "nextval('%s')" % self.quote_name(sequence_name),
                            }
                        },
                        [],
                    ),
                    (
                        self.sql_set_sequence_max % {
                            "table": self.quote_name(table),
                            "column": self.quote_name(column),
                            "sequence": self.quote_name(sequence_name),
                        },
                        [],
                    ),
                ],
            )
        else:
            return super(DatabaseSchemaEditor, self)._alter_column_type_sql(
                table, old_field, new_field, new_type
            )

    def _alter_field(self, model, old_field, new_field, old_type, new_type,
                     old_db_params, new_db_params, strict=False):
        super(DatabaseSchemaEditor, self)._alter_field(
            model, old_field, new_field, old_type, new_type, old_db_params,
            new_db_params, strict
        )
        # Added an index?
        if ((not old_field.db_index and new_field.db_index) or (not old_field.unique and new_field.unique)):
            db_type = new_field.db_type(connection=self.connection)
            if db_type is not None and (new_field.db_index or new_field.unique):
                # Fields with database column types of `varchar` and `text` need
                # a second index that specifies their operator class, which is
                # needed when performing correct LIKE queries outside the
                # C locale. See #12234.
                #
                # This code resambles the one found in `self._model_indexes_sql`.
                # The only difference is that here the statements are
                # executed immediately.
                #
                # The same doesn't apply to array fields such as varchar[size]
                # and text[size], so skip them.
                if '[' in db_type:
                    return
                if db_type.startswith('varchar'):
                    self.execute(self._create_index_sql(
                        model, [new_field], suffix='_like', sql=self.sql_create_varchar_index))
                elif db_type.startswith('text'):
                    self.execute(self._create_index_sql(
                        model, [new_field], suffix='_like', sql=self.sql_create_text_index))
        # Removed an index?
        if ((not new_field.db_index and old_field.db_index) or (not new_field.unique and old_field.unique)):
            # Find the index for this field.
            # This is needed because the BaseDatabaseSchemaEditor._alter_field
            # method doesn't drop the '_like' indexes.
            index_names = self._constraint_names(model, [old_field.column], index=True)
            for index_name in index_names:
                self.execute(self._delete_constraint_sql(self.sql_delete_index, model, index_name))
